import os

# chroma 不上传数据
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["DISABLE_TELEMETRY"] = "1"
os.environ["CHROMA_TELEMETRY_ENABLED"] = "false"

import json
import logging
import warnings
import yaml
import pandas as pd
from typing import Any, Dict, List, Tuple

# silence specific pydantic warnings about 'validate_default'
try:
    from pydantic._internal._generate_schema import UnsupportedFieldAttributeWarning
    warnings.filterwarnings("ignore", category=UnsupportedFieldAttributeWarning)
except Exception:
    warnings.filterwarnings(
        "ignore",
        message=r"The 'validate_default' attribute",
        category=Warning,
    )

# langchain LLM backends
from langchain_ollama import OllamaLLM, OllamaEmbeddings
from langchain_huggingface import HuggingFaceEmbeddings, HuggingFacePipeline
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline as hf_pipeline

# llama-index & chroma
import chromadb
from llama_index.core import Settings  # 全局
from llama_index.core import Document
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore  # 注意导入路径
from llama_index.llms.langchain import LangChainLLM
from llama_index.embeddings.langchain import LangchainEmbedding

# 日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    return ''.join(c if c.isalnum() else '_' for c in text)[:64]


def _apply_proxies_from_cfg(cfg: Dict[str, Any]):
    """根据配置设置进程代理环境变量，优先在模型下载/初始化前调用。"""
    http_proxy = cfg.get("HTTP_PROXY") or cfg.get("http_proxy")
    https_proxy = cfg.get("HTTPS_PROXY") or cfg.get("https_proxy")
    all_proxy = cfg.get("ALL_PROXY") or cfg.get("all_proxy")
    no_proxy = cfg.get("NO_PROXY") or cfg.get("no_proxy")

    def _set_env(key: str, val: str | None):
        if val:
            os.environ[key] = val

    # 同时设置大小写，兼容 requests/huggingface_hub 等
    for key, val in (
        ("HTTP_PROXY", http_proxy),
        ("http_proxy", http_proxy),
        ("HTTPS_PROXY", https_proxy),
        ("https_proxy", https_proxy),
        ("ALL_PROXY", all_proxy or http_proxy),
        ("all_proxy", all_proxy or http_proxy),
        ("NO_PROXY", no_proxy),
        ("no_proxy", no_proxy),
    ):
        _set_env(key, val)


class TopKLogSystem:
    def __init__(
            self,
            config_path: str = "./config/llm_config.yaml",
    ) -> None:
        """
        通过配置文件初始化系统。
        - config_path: YAML 配置文件路径，包含 provider、模型、代理、日志路径、系统提示与回答模板路径。
        """
        self.config_path = config_path

        # load provider config
        with open(config_path, "r", encoding="utf-8") as f:
            env_cfg = yaml.safe_load(f) or {}

        # 先应用代理，确保后续模型/权重下载、远程请求走代理
        _apply_proxies_from_cfg(env_cfg)

        provider = (env_cfg.get("LLM_PROVIDER") or "ollama").lower()

        # 从配置读取路径
        self.log_path = env_cfg.get("LOG_PATH", "./data/log")
        self.system_prompt_path = env_cfg.get("SYSTEM_PROMPT_PATH", "./config/system_prompt.yaml")
        self.response_template_path = env_cfg.get("RESPONSE_TEMPLATE_PATH", "./config/response_template.md")

        # 默认格式控制（可被 system_prompt.yaml 覆盖）
        self.max_parts_num: int = 3
        self.max_part_length: int = 50

        # 加载系统前置提示和回答模板
        self.system_prompt = self._load_system_prompt(self.system_prompt_path)
        self.response_template = self._load_response_template(self.response_template_path)

        # init models by provider
        self.provider = provider
        if provider == "transformers":
            tcfg = env_cfg.get("TRANSFORMERS_CONFIG", {})
            llm_model = tcfg.get("llm_model")
            device_map = tcfg.get("device_map", "auto")
            torch_dtype = tcfg.get("torch_dtype", "auto")
            trust_remote_code = bool(tcfg.get("trust_remote_code", False))
            max_new_tokens = int(tcfg.get("max_new_tokens", 512))
            temperature = float(tcfg.get("temperature", 0.7))
            top_p = float(tcfg.get("top_p", 0.95))
            repetition_penalty = float(tcfg.get("repetition_penalty", 1.1))
            do_sample = bool(tcfg.get("do_sample", True))

            embedding_name = tcfg.get("embedding_model")

            # embeddings via HuggingFace
            hf_embed = HuggingFaceEmbeddings(model_name=embedding_name)
            self.collection_name = f"log_collection_transformers_{_slugify(embedding_name)}"

            # LLM via transformers pipeline
            tokenizer = AutoTokenizer.from_pretrained(llm_model, trust_remote_code=trust_remote_code)
            model = AutoModelForCausalLM.from_pretrained(
                llm_model,
                device_map=device_map,
                torch_dtype=None if torch_dtype == "auto" else torch_dtype,
                trust_remote_code=trust_remote_code,
            )
            gen_pipe = hf_pipeline(
                task="text-generation",
                model=model,
                tokenizer=tokenizer,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                do_sample=do_sample,
            )
            hf_llm = HuggingFacePipeline(pipeline=gen_pipe)

            # register to llama-index via adapters
            Settings.llm = LangChainLLM(llm=hf_llm)
            Settings.embed_model = LangchainEmbedding(hf_embed)
        else:
            # default ollama
            ocfg = env_cfg.get("OLLAMA_CONFIG", {})
            llm_name = ocfg.get("model")
            embed_name = ocfg.get("embedding_model")

            self.embedding_model = OllamaEmbeddings(model=embed_name)
            ollama_llm = OllamaLLM(model=llm_name, temperature=0.1)
            self.collection_name = f"log_collection_ollama_{_slugify(embed_name)}"

            # register to llama-index via adapters as well
            Settings.llm = LangChainLLM(llm=ollama_llm)
            Settings.embed_model = LangchainEmbedding(self.embedding_model)  # 全局设置

        self.log_index = None
        self.vector_store = None
        self._build_vectorstore()  # 直接构建

    def _extract_format_limits(self, data: Dict[str, Any]) -> Tuple[int, int]:
        """从 system_prompt.yaml 的字典中提取 MAX_PARTS_NUM 与 MAX_PART_LENGTH。"""
        parts = data.get('MAX_PARTS_NUM')
        length = data.get('MAX_PART_LENGTH')
        try:
            if parts is not None:
                self.max_parts_num = max(1, min(10, int(parts)))
        except Exception:
            pass
        try:
            if length is not None:
                self.max_part_length = max(10, min(200, int(length)))
        except Exception:
            pass
        return self.max_parts_num, self.max_part_length

    def _load_system_prompt(self, path: str) -> str:
        """加载系统前置提示。支持：
        - 纯文本（整文件为字符串）
        - YAML 字典，优先字段 text；否则将所有键值按顺序拼接：
          Role, Mission, Guidelines, Constraints, Style, Tone, OutputLanguage, OutputRules 等；
          列表值会按 "- item" 逐行展开；其他未知键也会以 "Key: Value" 加入。
        同时提取 MAX_PARTS_NUM 与 MAX_PART_LENGTH 并存入实例变量。
        """
        try:
            if not path or not os.path.exists(path):
                raise FileNotFoundError("system_prompt 文件不存在")
            with open(path, 'r', encoding='utf-8') as f:
                raw = f.read()
            try:
                data = yaml.safe_load(raw)
            except Exception:
                data = None

            if isinstance(data, str):
                text = data.strip()
                if text:
                    logger.info(f"已加载系统前置提示(纯文本): {path}")
                    return text
            if isinstance(data, dict):
                # 提取格式限制
                self._extract_format_limits(data)

                # 优先使用 text 字段
                if isinstance(data.get('text'), str) and data.get('text').strip():
                    logger.info(f"已加载系统前置提示(text字段): {path}")
                    return data['text'].strip()

                order = [
                    'Role', 'Mission', 'Guidelines', 'Constraints',
                    'Style', 'Tone', 'OutputLanguage', 'OutputRules',
                    'Log', 'Query',  # 允许在 YAML 中内联占位符
                ]
                lines: List[str] = []

                def emit_kv(k: str, v: Any):
                    if v is None:
                        return
                    if isinstance(v, str):
                        v = v.strip()
                        if v:
                            lines.append(f"{k}: {v}")
                    elif isinstance(v, (int, float, bool)):
                        lines.append(f"{k}: {v}")
                    elif isinstance(v, list):
                        if v:
                            lines.append(f"{k}:")
                            for item in v:
                                if isinstance(item, (str, int, float, bool)):
                                    lines.append(f"- {str(item).strip()}")
                                elif isinstance(item, dict):
                                    lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
                    elif isinstance(v, dict):
                        # 展平一层
                        lines.append(f"{k}:")
                        for sk, sv in v.items():
                            emit_kv(f"  {sk}", sv)

                # 先按已知顺序输出
                for k in order:
                    if k in data:
                        emit_kv(k, data[k])

                # 再输出剩余未知键（排除我们消费过的控制键）
                consumed = set(order + ['text', 'MAX_PARTS_NUM', 'MAX_PART_LENGTH'])
                for k, v in data.items():
                    if k not in consumed:
                        emit_kv(k, v)

                text = "\n".join(lines).strip()
                if text:
                    logger.info(f"已加载系统前置提示(YAML结构): {path}")
                    return text

                raise ValueError("system_prompt.yaml 为空或无法解析有效内容")

            # 非 YAML / 解析失败，按纯文本处理
            text = raw.strip()
            if text:
                logger.info(f"已加载系统前置提示(回退纯文本): {path}")
                return text
        except Exception as e:
            logger.warning(f"加载系统前置提示失败（{path}）：{e}，将使用默认。")
        return "资深日志分析助手\n请按要求提供简洁且结构化的分析报告。"

    def _load_response_template(self, path: str) -> str:
        """加载回答模板（Markdown）。"""
        try:
            if path and os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content.strip():
                        logger.info(f"已加载回答模板: {path}")
                        return content
        except Exception as e:
            logger.warning(f"加载回答模板失败（{path}）：{e}，将使用默认模板。")
        return (
            "# 问题诊断\n1. \n2. \n3. \n\n"
            "# 可能原因（按概率降序排序）\n1. \n2. \n3. \n\n"
            "# 建议的排查步骤\n1. \n2. \n3. \n\n"
            "# 临时缓解措施\n1. \n2. \n3. \n\n"
            "# 最终修复建议\n1. \n2. \n3. \n"
        )

    # 加载数据并构建索引
    def _build_vectorstore(self):
        vector_store_path = "./data/vector_stores"
        os.makedirs(vector_store_path, exist_ok=True)  # exist_ok=True 目录存在时不报错

        chroma_client = chromadb.PersistentClient(path=vector_store_path)  # chromadb 持久化

        # 选择集合名称，避免不同嵌入维度冲突
        collection_name = getattr(self, "collection_name", None) or "log_collection_default"

        # ChromaVectorStore 将 collection 与 store 绑定
        # 也是将 Chroma 包装为 llama-index 的接口
        # StorageContext存储上下文， 包含 Vector Store、Document Store、Index Store 等
        log_collection = chroma_client.get_or_create_collection(collection_name)

        # 构建 log 库 index
        log_vector_store = ChromaVectorStore(chroma_collection=log_collection)
        log_storage_context = StorageContext.from_defaults(vector_store=log_vector_store)
        if log_documents := self._load_documents(self.log_path):
            self.log_index = VectorStoreIndex.from_documents(
                log_documents,
                storage_context=log_storage_context,
                show_progress=True,
            )
            logger.info(f"日志库索引构建完成，共 {len(log_documents)} 条日志")

    @staticmethod
    # 加载文档数据
    def _load_documents(data_path: str) -> List[Document]:
        if not os.path.exists(data_path):
            logger.warning(f"数据路径不存在: {data_path}")
            return []

        documents = []
        for file in os.listdir(data_path):
            ext = os.path.splitext(file)[1]
            if ext not in [".txt", ".md", ".json", ".jsonl", ".csv"]:
                continue

            file_path = f"{data_path}/{file}"
            try:
                if ext == ".csv":  # utf-8 的 csv
                    # 大型 csv 分块进行读取
                    chunk_size = 1000  # 每次读取1000行
                    for chunk in pd.read_csv(file_path, chunksize=chunk_size):
                        for row in chunk.itertuples(index=False):  # 无行号
                            content = str(row).replace("Pandas", " ")
                            documents.append(Document(text=content))
                else:  # .txt or .md, .json
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        doc = Document(text=content, )
                        documents.append(doc)
            except Exception as e:
                logger.error(f"加载文档失败 {file_path}: {e}")
        return documents

    # 检索相关日志
    def retrieve_logs(self, query: str, top_k: int = 10) -> List[Dict]:
        if not self.log_index:
            return []

        try:
            retriever = self.log_index.as_retriever(similarity_top_k=top_k)  # topK
            results = retriever.retrieve(query)

            formatted_results = []
            for result in results:
                formatted_results.append({
                    "content": result.text,
                    "score": result.score
                })
            return formatted_results
        except Exception as e:
            logger.error(f"日志检索失败: {e}")
            return []

    # LLM 生成响应
    def generate_response(self, query: str, context: Dict) -> str:
        prompt = self._build_prompt_text(query, context)  # 构建提示词

        try:
            # 通过全局 Settings.llm 调用，避免依赖实例属性
            resp = Settings.llm.complete(prompt)
            text = getattr(resp, "text", str(resp))
            return self._sanitize_output(text, query)
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return f"生成响应时出错: {str(e)}"

    def _build_prompt_text(self, query: str, context: Dict) -> str:
        # 构建日志上下文为纯文本
        log_context = "\n".join(
            f"日志 {i}: {(log.get('content') if isinstance(log, dict) else str(log))}"
            for i, log in enumerate(context, 1)
        )
        # 渲染 system_prompt 模板中的 {log_context} 与 {query}
        sp = self.system_prompt or ""
        has_lc = "{log_context}" in sp
        has_q = "{query}" in sp
        try:
            if has_lc or has_q:
                sp = sp.format(log_context=log_context, query=query)
        except Exception as e:
            logger.warning(f"渲染 system_prompt 占位符失败：{e}，使用未渲染文本")

        parts = [sp.strip()] if sp.strip() else []

        # 若 system_prompt 未包含对应内容，再追加默认段落，避免重复
        if not has_lc:
            parts.extend(["## 相关历史日志参考:", log_context, ""])
        if not has_q:
            parts.extend(["## 当前需要分析的问题:", query, ""])

        parts.extend([
            "请严格按照以下回答模板输出，不要回显上述提示或问题，仅填充内容：",
            self.response_template,
        ])
        return "\n".join(parts)

    def _sanitize_output(self, text: str, query: str) -> str:
        """
        输出清洗与标准化：
        - 从首个“### 问题诊断”或“# 问题诊断”开始截取。
        - 仅保留五个白名单段落；忽略“总结”等其他段落。
        - 将段落内容规范为最多 self.max_parts_num 条，超出截断；不足自动补齐占位。
        - 将 `-`/`*`/编号等项目统一为 1./2./3.，并将每条截断到 self.max_part_length 字以内（中文按字符截断）。
        """
        if not text:
            return text

        lines = text.splitlines()

        # 起始定位
        def find_start(headers) -> int:
            for idx, ln in enumerate(lines):
                s = ln.strip()
                if any(s.startswith(h) for h in headers):
                    return idx
            return -1

        start_idx = find_start(["### 问题诊断"])
        if start_idx == -1:
            start_idx = find_start(["# 问题诊断"])
        if start_idx > 0:
            lines = lines[start_idx:]

        # 白名单段落
        sections = [
            "问题诊断",
            "可能原因（按概率降序排序）",
            "建议的排查步骤",
            "临时缓解措施",
            "最终修复建议",
        ]

        # 收集每个段落的原始行
        collected: Dict[str, List[str]] = {s: [] for s in sections}
        current = None
        for ln in lines:
            st = ln.strip()
            if st.startswith('#'):
                name = st.lstrip('#').strip()
                key = next((k for k in sections if name.startswith(k)), None)
                current = key
                continue
            if current:
                # 跳过明显的指令或回显
                if st in {"", "1. ......", "2. ......", "3. ......"}:
                    continue
                if st == query.strip():
                    continue
                collected[current].append(st)

        # 规范化每个段落：提取前 N 条，转换为 1./2./...，并 <=max_len
        def normalize_items(items: List[str]) -> List[str]:
            N = max(1, int(self.max_parts_num))
            max_len = max(10, int(self.max_part_length))
            norm: List[str] = []
            # 提取候选：以 -, *, 数字., 中文数字等开头，或普通句子
            for raw in items:
                s = raw
                # 去除 markdown 列表前缀
                if s.startswith(('- ', '* ', '• ', '· ')):
                    s = s[2:].strip()
                # 去除有序列表前缀
                if len(s) > 2 and (s[0].isdigit() and s[1] in ['.', '、']):
                    s = s[2:].strip()
                # 去除代码反引号
                s = s.replace('`', '')
                if s:
                    norm.append(s)
                if len(norm) >= N * 2:  # 收集多一些，后面再截取N
                    break
            if not norm:
                return [f"{i}. 待确认" for i in range(1, N+1)]
            # 截取前N并限制长度
            out: List[str] = []
            for idx, s in enumerate(norm[:N], start=1):
                trimmed = s[:max_len]
                out.append(f"{idx}. {trimmed}")
            # 如不足N，补齐
            while len(out) < N:
                out.append(f"{len(out)+1}. 待确认")
            return out

        result_lines: List[str] = []
        for sec in sections:
            result_lines.append(f"# {sec}")
            items = normalize_items(collected.get(sec, []))
            result_lines.extend(items)
            result_lines.append("")

        return "\n".join(result_lines).strip()

    # 执行查询
    def query(self, query: str) -> Dict:
        log_results = self.retrieve_logs(query)
        response = self.generate_response(query, log_results)  # 生成响应

        return {
            "response": response,
            "retrieval_stats": len(log_results)
        }


# 示例使用
if __name__ == "__main__":
    # 初始化系统（仅需提供配置文件路径）
    system = TopKLogSystem(
        config_path="./config/llm_config.yaml",
    )

    # 执行查询
    query = "如何解决数据库连接池耗尽的问题？"
    result = system.query(query)

    print("查询:", query)
    print("响应:", result["response"])
    print("检索统计:", result["retrieval_stats"])
