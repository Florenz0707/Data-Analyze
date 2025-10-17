import os

# chroma 不上传数据
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["DISABLE_TELEMETRY"] = "1"
os.environ["CHROMA_TELEMETRY_ENABLED"] = "false"

import json
import logging
import pandas as pd
from typing import Any, Dict, List

# langchain
from langchain.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, SystemMessagePromptTemplate
from langchain_ollama import OllamaLLM, OllamaEmbeddings

# llama-index & chroma
import chromadb
from llama_index.core import Settings  # 全局
from llama_index.core import Document
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore  # 注意导入路径

# 日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TopKLogSystem:
    def __init__(
            self,
            log_path: str,
            llm: str,
            embedding_model: str,
    ) -> None:
        # init models
        self.embedding_model = OllamaEmbeddings(model=embedding_model)

        self.llm = OllamaLLM(model=llm, temperature=0.1)

        # init database
        Settings.llm = self.llm
        Settings.embed_model = self.embedding_model  # 全局设置

        self.log_path = log_path
        self.log_index = None
        self.vector_store = None
        self._build_vectorstore()  # 直接构建

    # 加载数据并构建索引
    def _build_vectorstore(self):
        vector_store_path = "./data/vector_stores"
        os.makedirs(vector_store_path, exist_ok=True)  # exist_ok=True 目录存在时不报错

        chroma_client = chromadb.PersistentClient(path=vector_store_path)  # chromadb 持久化

        # ChromaVectorStore 将 collection 与 store 绑定
        # 也是将 Chroma 包装为 llama-index 的接口
        # StorageContext存储上下文， 包含 Vector Store、Document Store、Index Store 等
        log_collection = chroma_client.get_or_create_collection("log_collection")

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
        prompt = self._build_prompt(query, context)  # 构建提示词

        try:
            response = self.llm.invoke(prompt)  # 调用LLM
            return response
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return f"生成响应时出错: {str(e)}"

            # 构建 prompt

    def _build_prompt(self, query: str, context: Dict) -> List[Dict]:
        # 系统消息 - 定义角色和任务
        system_message = SystemMessagePromptTemplate.from_template(" ")

        # 构建日志上下文
        log_context = "## 相关历史日志参考:\n"
        for i, log in enumerate(context, 1):
            log_context += f"日志 {i} : {log['content']}"

            # 用户消息
        user_message = HumanMessagePromptTemplate.from_template("""
            {log_context}
            ## 当前需要分析的问题:
            {query}

            请基于以上信息，提供详细的分析报告:
        """)

        # 创建提示词
        prompt = ChatPromptTemplate.from_messages([
            system_message,
            user_message
        ])

        return prompt.format_prompt(
            log_context=log_context,
            query=query
        ).to_messages()

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
    # 初始化系统
    system = TopKLogSystem(
        log_path="./data/log",
        llm="deepseek-r1:7b",
        embedding_model="bge-large:latest"
    )

    # 执行查询
    query = "如何解决数据库连接池耗尽的问题？"
    result = system.query(query)

    print("查询:", query)
    print("响应:", result["response"])
    print("检索统计:", result["retrieval_stats"])
