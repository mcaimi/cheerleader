# disassembly agent

from typing import Any
from pathlib import Path
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model, BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

# the agent class
class CheerleaderAIAgent(object):
    def __init__(self, config: dict[str, Any]):
        self.provider: str = config.get("provider")
        self.llm_model: str = config.get("llm_model")
        self.base_url: str = config.get("base_url")
        self.api_key: str = config.get("api_key")
        self.system_prompt: str = config.get("system_prompt")
        self.skills_path: Path = Path(config.get("skills_path"))

        # at the beginning, connection is empty
        self.llm_connector: BaseChatModel = None
        self.agent: CompiledStateGraph = None
        try:
            self.llm_connector: BaseChatModel = self._create_model_connector()
            self.agent: CompiledStateGraph = self._create_agent()
        except Exception as e:
            raise e from e

    # create a connection to a custom supported endpoint
    # specify model name and apikey
    def _create_model_connector(self) -> BaseChatModel:
        try:
            return init_chat_model(
                model=self.llm_model,
                model_provider=self.provider,
                api_key=self.api_key if self.api_key != "" else "sk-unprotected",
                base_url=self.base_url,
            )
        except Exception:
            import logging

            msg = f"Failed to initialize chat model: provider={self.provider}, base_url={self.base_url}, model={self.llm_model}"

            logging.critical(msg)
            raise Exception(msg)

    # create reverser agent with langchain deepagent library
    def _create_agent(self) -> CompiledStateGraph:
        try:
            # resolve skills path
            if not self.skills_path.exists():
                raise Exception(f"Skills dir {self.skills_path.absolute()} not found")

            # check connector
            if self.llm_connector is None:
                raise Exception(f"Connector for model {self.llm_model}, using URL {self.base_url} is not available.")
        
            # create a filesystem backend to load skills from
            from deepagents.backends.filesystem import FilesystemBackend
            fs_backend = FilesystemBackend(root_dir=self.skills_path.absolute().parent)

            # create the agent object
            return create_deep_agent(
                model=self.llm_connector,
                backend=fs_backend,
                system_prompt=self.system_prompt,
                skills=[self.skills_path.name]
            )
        except Exception:
            import logging
            msg = f"Failed to initialize Agent: model={self.llm_model}"

            logging.critical(msg)
            raise Exception(msg)

    # main method
    def invoke(self, user_prompt: str):
        payload = {
            "messages": [HumanMessage(content=user_prompt)]
        }
        return self.agent.invoke(payload)