#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成本地可用模型清单：config/available_local_models.json
- transformers: 从本机 Hugging Face 缓存扫描已下载模型（repo_type=model）
- ollama: 从 Ollama /api/tags 列表读取本地可用模型标签

用法：
  python config/generate_local_model.py                      # 自动探测缓存与默认 Ollama 地址
  python config/generate_local_model.py --hf-path D:/HuggingFace/hub
  python config/generate_local_model.py --ollama-host http://localhost:11434
  python config/generate_local_model.py --output config/available_local_models.json --overwrite
  python config/generate_local_model.py --transformers-only
  python config/generate_local_model.py --ollama-only

说明：
- 优先使用 huggingface_hub.scan_cache_dir()，若不支持则回退 scan_cache()
- HF 缓存根目录探测顺序：
  1) --hf-path 显式传入
  2) 让库自动探测（None）
  3) 环境变量 HF_HUB_CACHE
  4) 环境变量 HF_HOME
  5) 环境变量 HF_HOME/hub
- 输出 JSON 结构：
{
  "transformers": ["org/name", ...],
  "ollama": ["model:tag", ...]
}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple


def _import_hf_scan():
    try:
        from huggingface_hub import scan_cache_dir  # type: ignore
        return scan_cache_dir, "scan_cache_dir"
    except Exception:
        try:
            from huggingface_hub import scan_cache  # type: ignore
            return scan_cache, "scan_cache"
        except Exception as e:
            raise RuntimeError("未找到 huggingface_hub 扫描函数，请安装/升级 huggingface-hub") from e


def _gather_transformers(scan_func, mode: str, preferred_root: Optional[str] = None, verbose: bool = False) -> Tuple[
    List[str], Optional[str]]:
    candidates: List[Optional[str]] = []
    if preferred_root:
        candidates.append(preferred_root)
    candidates.append(None)  # 让库自动探测
    hub_cache = os.getenv("HF_HUB_CACHE")
    if hub_cache:
        candidates.append(hub_cache)
    hf_home = os.getenv("HF_HOME")
    if hf_home:
        candidates.append(hf_home)
        candidates.append(os.path.join(hf_home, "hub"))

    seen = set()
    result: List[str] = []
    used_root: Optional[str] = None

    for root in candidates:
        try:
            info = scan_func(root) if mode == "scan_cache_dir" else (scan_func(cache_dir=root) if root else scan_func())
            repo_infos = getattr(info, "repo_infos", None)
            if repo_infos is None:
                repo_infos = getattr(info, "repos", None)
            if repo_infos is None and isinstance(info, (list, tuple)):
                repo_infos = info
            for ri in (repo_infos or []):
                if getattr(ri, "repo_type", None) != "model":
                    continue
                rid = getattr(ri, "repo_id", None)
                if not rid:
                    owner = getattr(ri, "repo_owner", None)
                    name = getattr(ri, "repo_name", None)
                    rid = f"{owner}/{name}" if owner and name else (name or owner)
                if rid and rid not in seen:
                    seen.add(rid)
                    result.append(rid)
            if used_root is None:
                used_root = root if root is not None else "<auto>"
            if result:
                break
        except Exception as e:
            if verbose:
                print(f"[warn] HF 扫描失败: root={root or '<auto>'}: {e}", file=sys.stderr)
            continue

    return sorted(result), used_root


def _gather_ollama(host: str, timeout: float = 3.0, verbose: bool = False) -> List[str]:
    try:
        import requests
    except Exception as e:
        if verbose:
            print("[warn] requests 未安装，跳过 Ollama 扫描", file=sys.stderr)
        return []
    try:
        url = host.rstrip("/") + "/api/tags"
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json() or {}
        out: List[str] = []
        for m in data.get("models", []):
            name = m.get("name")
            if name:
                out.append(name)
        return sorted(out)
    except Exception as e:
        if verbose:
            print(f"[warn] Ollama 扫描失败: {e}", file=sys.stderr)
        return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 available_local_models.json")
    parser.add_argument("--output", "-o", type=str, default=os.path.join("config", "available_local_models.json"))
    parser.add_argument("--overwrite", action="store_true", help="若目标存在则覆盖写入")
    parser.add_argument("--hf-path", type=str, default=None, help="显式指定 HF 缓存根（如 D:/HuggingFace/hub）")
    parser.add_argument("--ollama-host", type=str, default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--transformers-only", action="store_true")
    parser.add_argument("--ollama-only", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    if args.transformers_only and args.ollama_only:
        print("--transformers-only 与 --ollama-only 不能同时使用", file=sys.stderr)
        return 2

    # 收集 transformers
    transformers: List[str] = []
    if not args.ollama_only:
        scan_func, mode = _import_hf_scan()
        transformers, used_root = _gather_transformers(scan_func, mode, args.hf_path, verbose=args.verbose)
        if args.verbose:
            print(json.dumps({
                "hf_scan_mode": mode,
                "hf_used_root": used_root,
                "transformers_found": len(transformers)
            }, ensure_ascii=False, indent=2))

    # 收集 ollama
    ollama: List[str] = []
    if not args.transformers_only:
        ollama = _gather_ollama(args.ollama_host, verbose=args.verbose)
        if args.verbose:
            print(json.dumps({
                "ollama_host": args.ollama_host,
                "ollama_found": len(ollama)
            }, ensure_ascii=False, indent=2))

    data = {
        "transformers": transformers,
        "ollama": ollama,
    }

    out_path = args.output
    if os.path.exists(out_path) and not args.overwrite:
        print(f"目标已存在：{out_path}（使用 --overwrite 允许覆盖）", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "output": out_path,
        "transformers": len(transformers),
        "ollama": len(ollama)
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
