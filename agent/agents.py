import sys
from abc import ABC, abstractmethod
from pathlib import Path
import logging

from aider.coders import Coder
from aider.models import Model
from aider.io import InputOutput
import re
import os

BEDROCK_REGION_MODEL_PRICING = {
    "moonshotai.kimi-k2.5": {
        "input_cost_per_token": 7.2e-07,
        "output_cost_per_token": 3.6e-06,
        "max_input_tokens": 262144,
        "max_output_tokens": 262144,
        "max_tokens": 262144,
        "mode": "chat",
        "litellm_provider": "bedrock",
        "supports_function_calling": True,
        "supports_system_messages": True,
        "supports_vision": True,
    },
}


def register_bedrock_arn_pricing(model_name: str) -> None:
    """Register pricing for Bedrock ARN-based inference profiles in litellm's cost map.

    When using custom inference profile ARNs (e.g. bedrock/converse/arn:aws:bedrock:...),
    litellm cannot match them to known model pricing. This resolves the ARN to the
    underlying model ID and copies its pricing into the cost map under the full ARN key.
    """
    if "arn:aws:bedrock:" not in model_name:
        return

    try:
        import boto3

        region = None
        for part in model_name.split(":"):
            if (
                part.startswith("ap-")
                or part.startswith("us-")
                or part.startswith("eu-")
                or part.startswith("sa-")
            ):
                region = part
                break

        client = boto3.client("bedrock", region_name=region or "ap-south-1")
        arn = model_name.split("bedrock/")[-1]
        if arn.startswith("converse/"):
            arn = arn[len("converse/") :]

        resp = client.get_inference_profile(inferenceProfileIdentifier=arn)
        models = resp.get("models", [])
        if models:
            underlying_model_id = models[0].get("modelArn", "").split("/")[-1]
            if not underlying_model_id:
                underlying_model_id = models[0].get("modelId", "")

            from aider.llm import litellm as aider_litellm
            import litellm

            for base_id, pricing in BEDROCK_REGION_MODEL_PRICING.items():
                if base_id in underlying_model_id or underlying_model_id in base_id:
                    litellm.model_cost[model_name] = pricing.copy()
                    litellm.model_cost[model_name]["litellm_provider"] = "bedrock"
                    return

            region_key = (
                f"bedrock/{region}/{underlying_model_id}"
                if region
                else f"bedrock/{underlying_model_id}"
            )
            if region_key in litellm.model_cost:
                litellm.model_cost[model_name] = litellm.model_cost[region_key].copy()
                return

            for key, val in litellm.model_cost.items():
                if underlying_model_id in key and "bedrock" in key:
                    litellm.model_cost[model_name] = val.copy()
                    return

    except Exception:
        pass


def handle_logging(logging_name: str, log_file: Path) -> None:
    """Handle logging for agent"""
    logger = logging.getLogger(logging_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger_handler = logging.FileHandler(log_file)
    logger_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(logger_handler)


class AgentReturn(ABC):
    def __init__(self, log_file: Path):
        self.log_file = log_file

        self.last_cost = 0.0


class Agents(ABC):
    def __init__(self, max_iteration: int):
        self.max_iteration = max_iteration

    @abstractmethod
    def run(self) -> AgentReturn:
        """Start agent"""
        raise NotImplementedError


class AiderReturn(AgentReturn):
    def __init__(self, log_file: Path):
        super().__init__(log_file)
        self.last_cost = self.get_money_cost()

    def get_money_cost(self) -> float:
        """Get accumulated money cost from log file"""
        last_cost = 0.0
        with open(self.log_file, "r") as file:
            for line in file:
                if "Tokens:" in line and "Cost:" in line:
                    match = re.search(
                        r"Cost: \$\d+\.\d+ message, \$(\d+\.\d+) session", line
                    )
                    if match:
                        last_cost = float(match.group(1))
        return last_cost


class AiderAgents(Agents):
    def __init__(
        self, max_iteration: int, model_name: str, cache_prompts: bool = False
    ):
        super().__init__(max_iteration)
        register_bedrock_arn_pricing(model_name)
        self.model = Model(model_name)
        self.cache_prompts = cache_prompts
        # Check if API key is set for the model
        if "bedrock" in model_name:
            api_key = os.environ.get("AWS_ACCESS_KEY_ID", None) or os.environ.get(
                "AWS_BEARER_TOKEN_BEDROCK", None
            )
        elif "gpt" in model_name or "openai" in model_name:
            api_key = os.environ.get("OPENAI_API_KEY", None)
        elif "claude" in model_name:
            api_key = os.environ.get("ANTHROPIC_API_KEY", None)
        elif "gemini" in model_name:
            api_key = os.environ.get("API_KEY", None)
        else:
            raise ValueError(f"Unsupported model: {model_name}")

        if not api_key:
            raise ValueError(
                "API Key Error: There is no API key associated with the model for this agent. "
                "Edit model_name parameter in .agent.yaml, export API key for that model, and try again."
            )

    def run(
        self,
        message: str,
        test_cmd: str,
        lint_cmd: str,
        fnames: list[str],
        log_dir: Path,
        test_first: bool = False,
        lint_first: bool = False,
    ) -> AgentReturn:
        """Start aider agent"""
        if test_cmd:
            auto_test = True
        else:
            auto_test = False
        if lint_cmd:
            auto_lint = True
        else:
            auto_lint = False
        log_dir = log_dir.resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        input_history_file = log_dir / ".aider.input.history"
        chat_history_file = log_dir / ".aider.chat.history.md"

        # Set up logging
        log_file = log_dir / "aider.log"
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

        # Redirect print statements to the log file
        sys.stdout = open(log_file, "a")
        sys.stderr = open(log_file, "a")

        # Configure httpx and backoff logging
        handle_logging("httpx", log_file)
        handle_logging("backoff", log_file)

        io = InputOutput(
            yes=True,
            input_history_file=input_history_file,
            chat_history_file=chat_history_file,
        )
        coder = Coder.create(
            main_model=self.model,
            fnames=fnames,
            auto_lint=auto_lint,
            auto_test=auto_test,
            lint_cmds={"python": lint_cmd},
            test_cmd=test_cmd,
            io=io,
            cache_prompts=self.cache_prompts,
        )
        coder.max_reflections = self.max_iteration
        coder.stream = True

        # Run the agent
        if test_first:
            test_errors = coder.commands.cmd_test(test_cmd)
            if test_errors:
                coder.run(test_errors)
        elif lint_first:
            coder.commands.cmd_lint(fnames=fnames)
        else:
            coder.run(message)

        # Close redirected stdout and stderr
        sys.stdout.close()
        sys.stderr.close()
        # Restore original stdout and stderr
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

        return AiderReturn(log_file)
