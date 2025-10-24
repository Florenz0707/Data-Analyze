#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成带注释的 llm_config.yaml 模板。
特点：
- 保留现有文件中的所有属性与注释（内置模板直接写出，避免 YAML 库丢失注释的问题）
- 保留合理默认值，可按需修改

用法：
- 直接覆盖生成：
  python config/generate_llm_config.py --output config/llm_config.yaml --force

- 生成到新文件：
  python config/generate_llm_config.py --output config/llm_config.yaml.template

- 打印到控制台：
  python config/generate_llm_config.py --print
"""

import argparse
import sys
from pathlib import Path

TEMPLATE = """# LLM 系统全局配置（@llm_config.yaml）
# 说明：
# - 本文件主要用于控制模型提供方、生成参数、日志与模板路径、以及格式约束等。
# - 你可以通过调整此文件来切换不同的模型后端（如 OLLAMA 或 Hugging Face transformers）。
# - 其中 LLM_MAX_PARTS_NUM 与 LLM_MAX_PART_LENGTH 会影响生成结果的条目数量与单条长度。

# （可选）网络代理设置：若需走代理以下载模型或访问远端服务，请在此设置。
HTTP_PROXY: "http://127.0.0.1:PORT"
HTTPS_PROXY: "http://127.0.0.1:PORT"

# 模型提供方：
# 可选值：
# - ollama         通过本地 OLLAMA 服务运行模型
# - transformers   通过 Hugging Face transformers 在本地加载模型
# - openai_compat  通过“OpenAI 兼容协议”调用云端（如 OpenAI/DeepSeek/自建兼容网关）
# - dashscope      通过阿里云百炼（Chat 走 OpenAI 兼容；Embeddings 走专用适配）
LLM_PROVIDER: ollama

# 可选：单独指定向量模型提供方；默认跟随 LLM_PROVIDER
# 可选值：auto | hf | ollama | openai_compat | dashscope
EMBEDDING_PROVIDER: auto

# 生成鲁棒性与输出限制
LLM_GENERATION_RETRIES: 2     # 生成失败重试次数（调用异常或输出过短时会重试）
LLM_MIN_OUTPUT_CHARS: 50      # 最小输出字符数（清洗后，若不足则尝试重试）
LLM_MAX_PARTS_NUM: 3          # 最大分析点数（清洗输出时保留的条目数量上限）
LLM_MAX_PART_LENGTH: 70       # 最大分析点长度（每条分析点的最大字符数，中文按字符截断）

# 检索配置
RESPONSE_TOP_K: 10              # 默认回答组织时的TopK条数（由系统使用）
# 兼容老配置：如仍存在 TOP_K，将被视作 RESPONSE_TOP_K 的别名（建议迁移后删除）

# 会话历史相关配置（控制是否、以及如何注入历史）
HISTORY_MODE: "auto"              # auto|on|off（默认 auto：按相似度判定是否使用历史）
HISTORY_MAX_TURNS: 8              # 候选历史的最近N轮
HISTORY_TOP_K: 3                  # 通过相似度挑选进入提示词的历史条数
HISTORY_SIM_THRESHOLD: 0.25       # 余弦相似度阈值（低于该值视为不相关）
HISTORY_MAX_TOKENS: 1000          # 历史片段在提示词中允许的最大token预算（超出则摘要/截断）

# 基础路径配置
LOG_PATH: "data/log"                                  # 日志文件目录
SYSTEM_PROMPT_PATH: "config/system_prompt.yaml"       # 系统提示词（模板）路径
RESPONSE_TEMPLATE_PATH: "config/response_template.md" # 回答模板（Markdown）路径

# OLLAMA 本地推理服务配置（当 LLM_PROVIDER=ollama 时生效）
OLLAMA_CONFIG:
  model: deepseek-r1:7b               # LLM 模型名称（需已在 OLLAMA 中可用）
  embedding_model: bge-large:latest   # 向量化模型名称
  host: http://localhost:11434        # OLLAMA 服务地址
  port: 11434                         # OLLAMA 端口
  api_key: ollama                     # 如有鉴权可在此配置
  api_key_secret: ollama

# Hugging Face transformers 配置
# 当 LLM_PROVIDER: transformers 时，以下配置生效
TRANSFORMERS_CONFIG:
  # 文本生成模型（Hugging Face 仓库ID或本地路径）
  # 例如：Qwen/Qwen2.5-1.5B-Instruct | Qwen/Qwen2.5-7B-Instruct | Deepseek-ai/DeepSeek-R1
  llm_model: "Qwen/Qwen2.5-1.5B-Instruct"

  # 设备与精度设置
  device: "auto"              # 可选："cuda" | "cpu" | "auto"
  device_map: "auto"          # accelerate 风格的 device map；通常用 "auto"
  torch_dtype: "auto"         # 可选："float16" | "bfloat16" | "auto"
  trust_remote_code: false    # 某些模型仓库需要自定义代码时需置为 true

  # 生成参数（默认值；可在调用时覆盖）
  max_new_tokens: 600         # 生成最大新标记数
  temperature: 0.7            # 采样温度
  top_p: 0.95                 # nucleus sampling 截断阈值
  repetition_penalty: 1.1     # 重复惩罚
  do_sample: true             # 是否启用采样

  # 性能优化开关（按需开启，需硬件/编译环境支持）
  use_flash_attention_2: false    # 若你的 torch 与 GPU 支持，可尝试开启

  # 向量模型（用于检索/RAG）
  # 可用值：sentence-transformers/all-MiniLM-L6-v2 | google/embeddinggemma-300m
  embedding_model: "sentence-transformers/all-MiniLM-L6-v2"
  embedding_device: "auto"        # 可选："cuda" | "cpu" | "auto"
  embedding_batch_size: 32        # 向量化批大小

# OpenAI 兼容协议配置（当 LLM_PROVIDER: openai_compat 时生效）
# 适用于 OpenAI 官方、DeepSeek（OpenAI兼容端点）、或自建 OpenAI 兼容网关
OPENAI_COMPAT_CONFIG:
  base_url: "https://api.openai.com/v1"
  api_key_env_name: "OPENAI_API_KEY"  # api_key.env 中的变量名
  organization: ""                    # 可选，OpenAI 可用
  timeout: 60                         # 请求超时秒数
  max_retries: 2                      # 单次调用内部重试次数

  # 文本生成模型
  model: "gpt-4o-mini"                # 例如：gpt-4o-mini, gpt-4o, deepseek-chat 等

  # 向量检索模型
  embedding_model: "text-embedding-3-large"
  embedding_dimensions: 3072          # OpenAI t-e-3-large 默认 3072，第三方兼容端点按需调整

# 阿里云百炼 DashScope 配置（当 LLM_PROVIDER: dashscope 时生效）
DASHSCOPE_CONFIG:
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  api_key_env_name: "DASHSCOPE_API_KEY"   # api_key.env 中的变量名
  timeout: 60
  max_retries: 2

  # Chat 模型（走 OpenAI 兼容 Chat 接口）
  chat_model: "qwen-turbo"                # 例如：qwen-turbo/qwen-plus/qwen-max 等

  # Embedding 模型（走专用适配器，避免兼容模式差异）
  embedding_model: "text-embedding-v4"    # 例如：text-embedding-v4
  embedding_dimensions: 1024              # v4 常见为 1024，如有变化按官方文档调整
"""


def write_content(path: Path, content: str, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"目标文件已存在：{path}（使用 --force 覆盖）")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 llm_config.yaml 模板（带注释与默认值）")
    parser.add_argument("--output", "-o", type=str, default="config/llm_config.yaml",
                        help="输出文件路径（默认：config/llm_config.yaml）")
    parser.add_argument("--force", "-f", action="store_true", help="若目标存在则覆盖写入")
    parser.add_argument("--print", "-p", action="store_true", help="仅打印模板到标准输出，不写文件")
    args = parser.parse_args(argv)

    if args.print:
        sys.stdout.write(TEMPLATE)
        if not TEMPLATE.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    out = Path(args.output)
    try:
        write_content(out, TEMPLATE, force=args.force)
    except FileExistsError as e:
        sys.stderr.write(str(e) + "\n")
        return 1

    print(f"已生成模板：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
