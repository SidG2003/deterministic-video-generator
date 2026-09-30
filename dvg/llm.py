"""
Provider-agnostic LLM call wrapper.

`generate_ir` (constrained/IR) and `generate_freeform` (freeform/Manim code)
both need to send a system-prompt + message-history chat request and get back
(text, token usage) in one uniform shape, regardless of which vendor sits
behind `model`. This module is the single seam that knows about the specific
SDKs, so both callers stay vendor-agnostic. The provider is inferred purely
from the model id -- "claude-*" -> Anthropic, "gpt-*"/"o1*"/"o3*"/"o4*" ->
OpenAI (or an OpenAI-compatible Azure OpenAI deployment) -- so there's no
separate --provider flag to keep in sync.

Token usage is always normalized to Anthropic's field names (input_tokens,
output_tokens, cache_creation_input_tokens, cache_read_input_tokens) so
RunLogger.record_tokens works unchanged for either provider. OpenAI/Azure have
no explicit cache-write step (their prompt caching is automatic and read-only
from the caller's perspective), so cache_creation_input_tokens is always 0
there; cache_read_input_tokens comes from usage.input_tokens_details.cached_tokens.

Needs, in the environment (or a .env file in the working directory):
  - ANTHROPIC_API_KEY, for "claude-*" models; or
  - OPENAI_API_KEY, for "gpt-*"/"o*" models against the public OpenAI API; or
  - Azure OpenAI deployments for "gpt-*"/"o*" models, configured as one or more
    numbered blocks AZURE_OPENAI_<N>_{ENDPOINT,DEPLOYMENT,API_KEY} (plus an
    optional per-block _API_VERSION, else the shared AZURE_OPENAI_API_VERSION).
    `--model` is matched against a block's DEPLOYMENT name, so different models
    can live on different Azure resources (each with its own endpoint + key). A
    legacy single block (AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY) is still
    honored as a fallback for any gpt/o model that matches no numbered block.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # populate os.environ from a .env file, before any client reads it

_OPENAI_PREFIXES = ("gpt", "o1", "o3", "o4")
_AZURE_MAX_BLOCKS = 20  # how many AZURE_OPENAI_<N>_ blocks we scan for


def provider_for(model: str) -> str:
    """Infer the backing provider from a model id."""
    m = model.lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(_OPENAI_PREFIXES):
        return "openai"
    raise ValueError(
        f"cannot infer a provider for model {model!r}; expected a 'claude-*' "
        "(Anthropic) or 'gpt-*'/'o*' (OpenAI/Azure OpenAI) model id"
    )


def _azure_deployments() -> dict[str, dict]:
    """Discover configured Azure deployments, keyed by deployment name (what you
    pass as `--model`). Reads numbered blocks AZURE_OPENAI_<N>_{ENDPOINT,
    DEPLOYMENT,API_KEY} with an optional per-block _API_VERSION, falling back to
    the shared AZURE_OPENAI_API_VERSION. Each block may point at a different
    Azure resource, so models can live on separate endpoints/keys."""
    shared_version = os.environ.get("AZURE_OPENAI_API_VERSION")
    out: dict[str, dict] = {}
    for i in range(1, _AZURE_MAX_BLOCKS + 1):
        prefix = f"AZURE_OPENAI_{i}_"
        endpoint = os.environ.get(prefix + "ENDPOINT")
        deployment = os.environ.get(prefix + "DEPLOYMENT")
        if not endpoint or not deployment:
            continue
        out[deployment] = {
            "endpoint": endpoint,
            "api_key": os.environ.get(prefix + "API_KEY"),
            "api_version": os.environ.get(prefix + "API_VERSION") or shared_version,
        }
    return out


def make_client(model: str):
    """Construct the right SDK client for `model` (each SDK is lazily imported so
    rendering-only code paths don't require either as a dependency). For the
    OpenAI family, a numbered Azure block whose DEPLOYMENT matches `model` wins;
    otherwise a legacy single Azure block (AZURE_OPENAI_ENDPOINT) is used; else
    the public OpenAI API."""
    if provider_for(model) == "anthropic":
        import anthropic

        return anthropic.Anthropic()

    import openai

    # 1) explicit per-deployment Azure block (matched by deployment name)
    deployments = _azure_deployments()
    dep = deployments.get(model)
    if dep:
        if not dep["api_version"]:
            raise RuntimeError(
                f"Azure deployment {model!r} has no api_version "
                "(set AZURE_OPENAI_<N>_API_VERSION or the shared AZURE_OPENAI_API_VERSION)"
            )
        return openai.AzureOpenAI(
            azure_endpoint=dep["endpoint"],
            api_version=dep["api_version"],
            azure_ad_token=dep["api_key"],  # this gateway wants the secret in the auth-bearer header, not api-key
        )

    # 2) legacy single Azure block (any gpt/o model routes here if set)
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if endpoint:
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION")
        if not api_version:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT is set but AZURE_OPENAI_API_VERSION is missing")
        return openai.AzureOpenAI(
            azure_endpoint=endpoint,
            api_version=api_version,
            azure_ad_token=os.environ.get("AZURE_OPENAI_API_KEY"),
        )

    # 3) public OpenAI API if a key is present
    if os.environ.get("OPENAI_API_KEY"):
        return openai.OpenAI()

    # Azure blocks exist but none matched, and there's no public key: fail clearly
    # instead of hitting the public API with a deployment-style model id.
    if deployments:
        known = ", ".join(sorted(deployments))
        raise RuntimeError(
            f"model {model!r} matches no configured Azure deployment (known: {known}); "
            "add an AZURE_OPENAI_<N>_ block for it, or set OPENAI_API_KEY for the public API"
        )
    return openai.OpenAI()


def complete(client, model: str, system: str, messages: list[dict],
             max_tokens: int = 16000) -> tuple[str, dict, str]:
    """Send one chat turn and return (text, usage_dict, served_model). `messages`
    holds only user/assistant turns; `system` is the (cached, where supported)
    system prompt. `served_model` is the model id the API reports actually
    serving the request (on Azure this is the underlying model, not the
    deployment name), falling back to the requested `model`."""
    if provider_for(model) == "anthropic":
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = response.usage
        tokens = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        }
        return text, tokens, getattr(response, "model", None) or model

    # OpenAI / Azure OpenAI: the Responses API is the unified interface for both.
    response = client.responses.create(
        model=model,
        instructions=system,
        input=messages,
        max_output_tokens=max_tokens,
    )
    text = response.output_text
    usage = response.usage
    input_details = getattr(usage, "input_tokens_details", None)
    tokens = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": getattr(input_details, "cached_tokens", 0) or 0,
    }
    return text, tokens, getattr(response, "model", None) or model
