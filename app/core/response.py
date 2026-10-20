from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    status: str = Field(default="success", description="Status string: 'success' or 'error'")
    message: str = Field(default="Operation completed successfully", description="Response message")
    data: Optional[T] = Field(default=None, description="Response payload")


def api_response(data: Any = None, message: str = "Success", status: str = "success") -> dict:
    return {
        "status": status,
        "message": message,
        "data": data
    }
