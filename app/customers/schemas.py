from app.vendors.schemas import ResturantResponse, MenuResponse
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.auth.schemas import UserPublicResponse


class CustomerOrderCreate(BaseModel):
    restaurant_id: int
    items: list[dict]


class CategoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: str
    total_items: int


class DeliveryAddressBase(BaseModel):
    address: str


class DeliveryAddressRequest(DeliveryAddressBase):
    is_primary: bool


class DeliveryAddressResponse(DeliveryAddressBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_primary: bool
    user_id: int
    user: UserPublicResponse


class DeliveryAddressCreatedResponse(BaseModel):
    message: str | None = None
    address: DeliveryAddressResponse


class OrderItemCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "menu_id": 1,
                "quantity": 2,
                "selected_options": [3, 7]
            }
        }
    )

    menu_id: int = Field(
        description="ID of the menu item to order."
    )
    quantity: int = Field(
        default=1,
        ge=1,
        description="How many of this item. Must be at least 1."
    )
    selected_options: list[int] = Field(
        default_factory=list,
        description=(
            "IDs of MenuOptionItems to customise this item (e.g. extra toppings, drink size). "
            "Find these IDs under the menu's 'menus_options[].options' field. "
            "Pass an empty list [] if no customisations are needed."
        )
    )


class SelectedOptionResponse(BaseModel):
    id: int
    name: str
    additional_price: int


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    menu_id: int
    menu: MenuResponse
    unit_price: int
    quantity: int
    total_price: int
    selected_options: list[SelectedOptionResponse] | None = None


class OrderCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "restaurant_id": 1,
                "delivery_address_id": 1,
                "items": [
                    {"menu_id": 1, "quantity": 2, "selected_options": [3, 7]},
                    {"menu_id": 4, "quantity": 1, "selected_options": []}
                ]
            }
        }
    )

    restaurant_id: int = Field(
        description="ID of the restaurant you are ordering from."
    )
    delivery_address_id: int = Field(
        description="ID of your saved delivery address."
    )
    items: list[OrderItemCreate] = Field(
        min_length=1,
        description="List of items to order. Must contain at least one item."
    )


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    customer_id: int
    restaurant_id: int
    rider_id: int | None = None
    delivery_address_id: int
    delivery_address: DeliveryAddressResponse | None = None
    status: str
    payment_status: str
    subtotal: int
    delivery_fee: int
    service_fee: int
    total_amount: int
    items: list[OrderItemResponse]
    created_at: datetime


class OrderCreatedCheckoutResponse(BaseModel):
    message: str | None = None
    order: OrderResponse
    payment_url: str


class ReviewBase(BaseModel):
    rating: int
    comment: str
    rider_rating: int | None = None
    rider_comment: str | None = None


class ReviewRequest(ReviewBase):
    pass


class ReviewResponse(ReviewBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user: UserPublicResponse
    order: OrderResponse
    restaurant: ResturantResponse
    created_at: datetime


class ReviewCreatedResponse(BaseModel):
    message: str | None = None
    review: ReviewResponse


class CartItemAddRequest(BaseModel):
    quantity: int = Field(default=1, ge=1)
    selected_options: list[int] = Field(default_factory=list)


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    menu: MenuResponse
    unit_price: int
    quantity: int
    total_price: int
    selected_options: list[SelectedOptionResponse] | None = None


class CartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    restaurant_id: int
    items: list[CartItemResponse]
    total_amount: int
    restaurant: ResturantResponse
