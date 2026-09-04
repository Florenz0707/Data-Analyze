"""HuggingFace Transformers Provider adapter."""

from __future__ import annotations

from typing import Any

from .base import BaseProviderAdapter, ModelCapabilities, ProviderMetadata


class TransformersAdapter(BaseProviderAdapter):
    metadata = ProviderMetadata(
        name="transformers",
        embedding_aliases=("hf",),
        llm_config_section="TRANSFORMERS_CONFIG",
        embedding_config_section="TRANSFORMERS_CONFIG",
        llm_model_key="llm_model",
        embedding_model_key="embedding_model",
    )
    capabilities = ModelCapabilities(streaming=True)

    def build_llm(self, config: dict[str, Any], *, model: str | None = None) -> Any:
        from langchain_huggingface import HuggingFacePipeline
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from transformers import pipeline as hf_pipeline

        tcfg = self._section(embedding=False, config=config)
        llm_model = self.resolve_model(config, model=model)
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
        loaded_model = AutoModelForCausalLM.from_pretrained(
            llm_model,
            device_map=device_map,
            torch_dtype=None if torch_dtype == "auto" else torch_dtype,
            trust_remote_code=trust_remote_code,
        )
        gen_pipe = hf_pipeline(
            task="text-generation",
            model=loaded_model,
            tokenizer=tokenizer,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            do_sample=do_sample,
        )
        return HuggingFacePipeline(pipeline=gen_pipe)

    def build_embedding(
        self, config: dict[str, Any], *, model: str | None = None
    ) -> tuple[Any, str]:
        from langchain_huggingface import HuggingFaceEmbeddings

        embedding_name = self.resolve_model(config, embedding=True, model=model)
        if not embedding_name:
            embedding_name = "sentence-transformers/all-MiniLM-L6-v2"
        return HuggingFaceEmbeddings(model_name=embedding_name), embedding_name
