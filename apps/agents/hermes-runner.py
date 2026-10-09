"""One isolated, tool-free Hermes turn using credentials supplied on stdin."""

from __future__ import annotations

import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path


def main() -> int:
    request = json.load(sys.stdin)
    source = Path(os.environ["HERMES_AGENT_DIR"]).resolve()
    sys.path.insert(0, str(source))

    # Hermes prints startup notices even in quiet mode. Keep the broker's stdout
    # protocol to one JSON response; the broker discards child stderr.
    with redirect_stdout(sys.stderr):
        from run_agent import AIAgent

        agent = AIAgent(
            base_url=request["platform_api_base"],
            api_key=request["api_key"],
            provider="custom",
            api_mode="chat_completions",
            model=request["model"],
            max_tokens=4096,
            max_iterations=1,
            enabled_toolsets=[],
            disabled_toolsets=["all"],
            quiet_mode=True,
            ephemeral_system_prompt="You are a text-only assistant. Answer the user's request directly. You have no tools.",
            skip_context_files=True,
            load_soul_identity=False,
            skip_memory=True,
            skip_background_review=True,
            fallback_model=None,
        )
        try:
            if request.get("proxy_token"):
                headers = dict(agent._client_kwargs.get("default_headers") or {})
                headers["X-Rubai-Proxy-Token"] = request["proxy_token"]
                agent._client_kwargs["default_headers"] = headers
                agent.client.close()
                agent.client = agent._create_openai_client(agent._client_kwargs, reason="agent_runtime_proxy", shared=True)
            result = agent.run_conversation(request["prompt"])
            if result.get("error") or result.get("failed") or result.get("completed") is False:
                raise RuntimeError("Hermes could not complete this request")
            text = result.get("final_response") or ""
        finally:
            try:
                agent.shutdown_memory_provider()
            except Exception:
                pass
            agent.close()
    sys.stdout.write(json.dumps({"text": text}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        sys.stderr.write("Agent request failed.\n")
        raise SystemExit(1)
