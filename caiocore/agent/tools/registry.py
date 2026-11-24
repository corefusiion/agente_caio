"""Tool registry for dynamic tool management."""

from typing import Any
from importlib import import_module

from caiocore.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.
    
    Allows dynamic registration and execution of tools.
    """
    
    # Unknown tools, MCP tools and mutating actions fail closed. The old
    # autonomous flag and chat metadata are not an approval mechanism.
    _READ_ONLY = {
        "web_search": ("web", "WebSearchTool"),
        # web_fetch follows unvalidated redirects (SSRF); keep disabled.
        "get_weather": ("weather", "WeatherTool"),
        "email_read": ("email_read", "EmailReadTool"),
        "get_system_status": ("system_status", "SystemStatusTool"),
        "read_skill": ("skill_read", "ReadSkillTool"),
    }
    _FILE_READ_ONLY = {
        "read_file": ("filesystem", "ReadFileTool"),
        "list_dir": ("filesystem", "ListDirTool"),
    }

    def __init__(self, enable_workspace_file_tools: bool = False):
        self._tools: dict[str, Tool] = {}
        self._enable_workspace_file_tools = enable_workspace_file_tools

    def _permitted(self, name: str, tool: Tool) -> bool:
        if tool.requires_approval:
            return False
        spec = self._READ_ONLY.get(name)
        if self._enable_workspace_file_tools and name in self._FILE_READ_ONLY:
            spec = self._FILE_READ_ONLY[name]
            if not getattr(tool, "_workspace", None) or not getattr(tool, "_allowed_dir", None):
                return False
            if tool._allowed_dir.resolve() != tool._workspace.resolve():
                return False
        if spec is None:
            return False
        module, cls = spec
        return type(tool) is getattr(import_module(f"caiocore.agent.tools.{module}"), cls)
    
    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
    
    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
    
    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools
    
    def get_definitions(self, allowed_tools: list[str] | None = None) -> list[dict[str, Any]]:
        """Get tool definitions in OpenAI format, optionally filtered.
        
        Args:
            allowed_tools: If provided, only include tools with these names.
                           If None, return all tools (full Caio access).
        """
        if allowed_tools is not None:
            return [
                tool.to_schema() for name, tool in self._tools.items()
                if name in allowed_tools
            ]
        return [tool.to_schema() for tool in self._tools.values()]
    
    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """
        Execute a tool by name with given parameters.
        
        Args:
            name: Tool name.
            params: Tool parameters.
        
        Returns:
            Tool execution result as string.
        
        Raises:
            KeyError: If tool not found.
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            if not self._permitted(name, tool):
                return (f"BLOQUEADO: '{name}' não foi executada. Não existe mecanismo "
                        "confiável de aprovação para esta ação; confirmação genérica não autoriza a execução.")
            return await tool.execute(**params)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"
    
    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())
    
    def __len__(self) -> int:
        return len(self._tools)
    
    def __contains__(self, name: str) -> bool:
        return name in self._tools
