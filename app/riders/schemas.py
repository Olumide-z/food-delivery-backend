from pydantic import BaseModel
from app.models import OrderStatus


class DeliveryUpdate(BaseModel):
    status: OrderStatus
