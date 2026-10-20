# Vendor Pydantic Schemas (Menu items, Restaurant management)
from app.models import OrderStatus
from app.auth.schemas import UserPublicResponse
from pydantic import ConfigDict
from pydantic import Field
from pydantic import BaseModel


class ResturantBase(BaseModel):
    name: str = Field(min_length=3, max_length=50)
    description: str = Field(min_length=1, max_length=255)
    address: str = Field(min_length=3, max_length=255)
    phone: str = Field(min_length=10, max_length=20)
    email: str | None = Field(max_length=120)
    website: str | None = Field(max_length=120)
    logo: str | None = None
    cover_image: str | None = None


class ResturantCreateRequest(ResturantBase):
    pass


class ResturantResponse(ResturantBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    rating: float
    review_count: int
    user: UserPublicResponse


class ResturantUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=50)
    description: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = Field(default=None, min_length=3, max_length=255)
    phone: str | None = Field(default=None, min_length=10, max_length=20)
    email: str | None = Field(default=None, max_length=120)
    website: str | None = Field(default=None, max_length=120)
    logo: str | None = None
    cover_image: str | None = None


class PaginatedResturantsResponse(BaseModel):
    restaurants: list[ResturantResponse]
    total: int
    skip: int
    limit: int
    has_more: bool

# Menu Option Item Schemas


class MenuOptionItemBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    additional_price: int = Field(default=0, ge=0)
    is_available: bool = Field(default=True)


class MenuOptionItemCreate(MenuOptionItemBase):
    pass


class MenuOptionItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    additional_price: int | None = Field(default=None, ge=0)
    is_available: bool | None = None


class MenuOptionItemResponse(MenuOptionItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    menu_group_id: int


# Menu Option Group Schemas


class MenuOptionGroupBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_required: bool = Field(default=False)
    min_selectable: int = Field(default=0, ge=0)
    max_selectable: int = Field(default=1, ge=1)


class MenuOptionGroupCreate(MenuOptionGroupBase):
    options: list[MenuOptionItemCreate] = Field(default_factory=list)


class MenuOptionGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_required: bool | None = None
    min_selectable: int | None = Field(default=None, ge=0)
    max_selectable: int | None = Field(default=None, ge=1)


class MenuOptionGroupResponse(MenuOptionGroupBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    menu_id: int
    options: list[MenuOptionItemResponse] = Field(default_factory=list)


# Menu Schemas


class MenuBase(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    price: int = Field(default=0, ge=0)
    image_url: str | None = None
    category: str | None = Field(default=None, max_length=50)
    is_available: bool = Field(default=True)
    is_vegetarian: bool = Field(default=False)
    is_spicy: bool = Field(default=False)
    prep_time_minutes: int | None = Field(default=None, ge=0)


class MenuCreationRequest(MenuBase):
    option_groups: list[MenuOptionGroupCreate] = Field(default_factory=list)


class MenuUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    price: int | None = Field(default=None, ge=0)
    image_url: str | None = None
    category: str | None = Field(default=None, max_length=50)
    is_available: bool | None = None
    is_vegetarian: bool | None = None
    is_spicy: bool | None = None
    prep_time_minutes: int | None = Field(default=None, ge=0)


class MenuResponse(MenuBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    restaurant_id: int
    menus_options: list[MenuOptionGroupResponse] = Field(default_factory=list)


class PaginatedMenusResponse(BaseModel):
    menus: list[MenuResponse]
    total: int
    skip: int
    limit: int
    has_more: bool


class VendorOrderStatusUpdate(BaseModel):
    status: OrderStatus


class VendorAnalyticResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_orders: int
    total_revenue: float
    pending_orders: int
    active_orders: int
    completed_orders: int
    cancelled_orders: int
    top_selling_items: list[str] = Field(default_factory=list)
