import os
from typing import Any, Dict, List, Tuple


# 延迟导入，按需依赖，避免环境缺少某些后端时报错

def _maybe_load_dotenv():
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    # 优先加载项目根目录下的 api_key.env（不报错）
    if os.path.exists("api_key.env"):
        load_dotenv("api_key.env")
    # 允许 .env 作为补充
    if os.path.exists(".env"):
        load_dotenv(".env")


def _slugify(text: str) -> str:
    return ''.join(c if c.isalnum() else '_' for c in str(text))[:64]


# ============== LLM 构建（独立） ==============

def build_llm_by(provider: str, env_cfg: Dict[str, Any]):
    p = (provider or "").lower()

    if p == "transformers":
        from langchain_huggingface import HuggingFacePipeline
        from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline as hf_pipeline

        tcfg = env_cfg.get("TRANSFORMERS_CONFIG", {})
        llm_model = tcfg.get("llm_model")
        if not llm_model:
            raise ValueError("TRANSFORMERS_CONFIG.llm_model 不能为空")

        device_map = tcfg.get("device_map", "auto")
        torch_dtype = tcfg.get("torch_dtype", "auto")
        trust_remote_code = bool(tcfg.get("trust_remote_code", False))
        max_new_tokens = int(tcfg.get("max_new_tokens", 512))
        temperature = float(tcfg.get("temperature", 0.7))
        top_p = float(tcfg.get("top_p", 0.95))
        repetition_penalty = float(tcfg.get("repetition_penalty", 1.1))
        do_sample = bool(tcfg.get("do_sample", True))

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
        return HuggingFacePipeline(pipeline=gen_pipe)

    if p == "ollama":
        from langchain_ollama import OllamaLLM
        ocfg = env_cfg.get("OLLAMA_CONFIG", {})
        llm_name = ocfg.get("model")
        if not llm_name:
            raise ValueError("OLLAMA_CONFIG.model 不能为空")
        return OllamaLLM(model=llm_name, temperature=0.1)

    if p == "openai_compat":
        _maybe_load_dotenv()
        from langchain_openai import ChatOpenAI
        cfg = env_cfg.get("OPENAI_COMPAT_CONFIG", {})
        base_url = cfg.get("base_url") or os.getenv("OPENAI_BASE_URL")
        api_key_env_name = cfg.get("api_key_env_name", "OPENAI_API_KEY")
        api_key = os.getenv(api_key_env_name)
        if not api_key:
            raise RuntimeError(f"未找到 API Key: 请设置 {api_key_env_name}")
        organization = cfg.get("organization") or os.getenv("OPENAI_ORG")
        timeout = int(cfg.get("timeout", 60))
        max_retries = int(cfg.get("max_retries", 2))
        model = cfg.get("model", "gpt-4o-mini")
        return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, organization=organization, timeout=timeout,
                          max_retries=max_retries)

    if p == "dashscope":
        _maybe_load_dotenv()
        from langchain_openai import ChatOpenAI
        cfg = env_cfg.get("DASHSCOPE_CONFIG", {})
        base_url = cfg.get("base_url") or os.getenv("DASHSCOPE_BASE_URL")
        api_key_env_name = cfg.get("api_key_env_name", "DASHSCOPE_API_KEY")
        api_key = os.getenv(api_key_env_name)
        if not api_key:
            raise RuntimeError(f"未找到 API Key: 请设置 {api_key_env_name}")
        timeout = int(cfg.get("timeout", 60))
        max_retries = int(cfg.get("max_retries", 2))
        chat_model = cfg.get("chat_model", "qwen-turbo")
        return ChatOpenAI(model=chat_model, api_key=api_key, base_url=base_url, timeout=timeout,
                          max_retries=max_retries)

    raise ValueError(f"不支持的 LLM provider: {provider}")


# ============== Embedding 构建（独立） ==============

def build_embedding_by(provider: str, env_cfg: Dict[str, Any]) -> Tuple[object, str]:
    p = (provider or "").lower()

    if p == "hf" or p == "transformers":
        from langchain_huggingface import HuggingFaceEmbeddings
        tcfg = env_cfg.get("TRANSFORMERS_CONFIG", {})
        embedding_name = tcfg.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2")
        embed = HuggingFaceEmbeddings(model_name=embedding_name)
        return embed, embedding_name

    if p == "ollama":
        from langchain_ollama import OllamaEmbeddings
        ocfg = env_cfg.get("OLLAMA_CONFIG", {})
        embed_name = ocfg.get("embedding_model")
        if not embed_name:
            raise ValueError("OLLAMA_CONFIG.embedding_model 不能为空")
        embed = OllamaEmbeddings(model=embed_name)
        return embed, embed_name

    if p == "openai_compat":
        _maybe_load_dotenv()
        from langchain_openai import OpenAIEmbeddings
        cfg = env_cfg.get("OPENAI_COMPAT_CONFIG", {})
        base_url = cfg.get("base_url") or os.getenv("OPENAI_BASE_URL")
        api_key_env_name = cfg.get("api_key_env_name", "OPENAI_API_KEY")
        api_key = os.getenv(api_key_env_name)
        if not api_key:
            raise RuntimeError(f"未找到 API Key: 请设置 {api_key_env_name}")
        organization = cfg.get("organization") or os.getenv("OPENAI_ORG")
        timeout = int(cfg.get("timeout", 60))
        max_retries = int(cfg.get("max_retries", 2))
        emb_model = cfg.get("embedding_model", "text-embedding-3-large")
        emb_dims = cfg.get("embedding_dimensions")
        kwargs: Dict[str, Any] = {
            "model": emb_model,
            "api_key": api_key,
            "base_url": base_url,
            "organization": organization,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if emb_dims:
            kwargs["dimensions"] = int(emb_dims)
        embed = OpenAIEmbeddings(**kwargs)
        return embed, emb_model

    if p == "dashscope":
        _maybe_load_dotenv()
        from langchain_core.embeddings import Embeddings as LCEmbeddings
        from openai import OpenAI as OpenAIClient
        cfg = env_cfg.get("DASHSCOPE_CONFIG", {})
        base_url = cfg.get("base_url") or os.getenv("DASHSCOPE_BASE_URL")
        api_key_env_name = cfg.get("api_key_env_name", "DASHSCOPE_API_KEY")
        api_key = os.getenv(api_key_env_name)
        if not api_key:
            raise RuntimeError(f"未找到 API Key: 请设置 {api_key_env_name}")
        timeout = int(cfg.get("timeout", 60))
        max_retries = int(cfg.get("max_retries", 2))
        emb_model = cfg.get("embedding_model", "text-embedding-v4")

        class DashScopeEmbeddings(LCEmbeddings):
            def __init__(self, model: str, api_key: str, base_url: str, timeout: int, max_retries: int):
                self.model = model
                self.client = OpenAIClient(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)

            def embed_documents(self, texts: List[str], **kwargs) -> List[List[float]]:
                if not texts:
                    return []
                resp = self.client.embeddings.create(model=self.model, input=texts)
                return [item.embedding for item in resp.data]

            def embed_query(self, text: str, **kwargs) -> List[float]:
                resp = self.client.embeddings.create(model=self.model, input=text)
                return resp.data[0].embedding

        embed = DashScopeEmbeddings(emb_model, api_key, base_url, timeout, max_retries)
        return embed, emb_model

    raise ValueError(f"不支持的 Embedding provider: {provider}")


# ============== 统一对外工厂（LLM + Embedding） ==============

def build_providers(env_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """根据配置构造 LLM 与 Embedding 提供方。
    - LLM_PROVIDER 控制对话模型
    - EMBEDDING_PROVIDER 控制向量模型；当为 auto 时，退化为跟随 LLM_PROVIDER
    返回：{"llm": <LangChain LLM>, "embedding": <LangChain Embeddings>, "collection_name": str}
    """
    llm_provider = (env_cfg.get("LLM_PROVIDER") or "ollama").lower()
    emb_provider_cfg = (env_cfg.get("EMBEDDING_PROVIDER") or "auto").lower()
    emb_provider = llm_provider if emb_provider_cfg in ("", "auto") else emb_provider_cfg

    llm = build_llm_by(llm_provider, env_cfg)
    embedding, emb_model_name = build_embedding_by(emb_provider, env_cfg)

    # 允许通过配置显式指定集合名；否则按提供方与模型名生成隔离后的默认集合名
    # 使用嵌入模型名派生集合名，弃用外部覆盖参数，确保同模型自动复用
    collection_name = f"log_collection_{_slugify(emb_model_name)}"

    return {
        "llm": llm,
        "embedding": embedding,
        "collection_name": collection_name,
    }
