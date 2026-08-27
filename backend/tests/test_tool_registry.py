from pydantic import BaseModel

from app.core.errors import ApplicationError
from app.harness.tools import ToolRegistry, ToolSpec


class EchoInput(BaseModel):
    value: str


async def test_tool_registry_validates_and_invokes() -> None:
    registry = ToolRegistry()

    async def echo(arguments: BaseModel) -> dict[str, object]:
        request = EchoInput.model_validate(arguments)
        return {"value": request.value}

    registry.register(
        ToolSpec(
            name="echo",
            version="1.0.0",
            description="Echo validated input.",
            input_model=EchoInput,
            handler=echo,
        )
    )

    assert await registry.invoke("echo", {"value": "ok"}) == {"value": "ok"}
    assert registry.describe()[0]["name"] == "echo"


async def test_unknown_tool_is_rejected() -> None:
    registry = ToolRegistry()
    try:
        await registry.invoke("missing", {})
    except ApplicationError as exc:
        assert exc.code == "tool_not_found"
    else:
        raise AssertionError("Unknown tool must raise ApplicationError")

