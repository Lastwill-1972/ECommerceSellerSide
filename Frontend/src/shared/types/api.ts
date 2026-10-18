export type IdentityRole =
  | 'PLATFORM_ADMIN'
  | 'MERCHANT_ADMIN'
  | 'SHOP_OWNER'
  | 'SHOP_EMPLOYEE'
  | 'NONE'

/** All backend BIGINT identifiers are represented as decimal strings. */
export interface UserSummary {
  id: string
  username: string
  email: string
}

export interface CurrentUser extends UserSummary {
  role: IdentityRole
  merchant_id: string | null
  shop_id: string | null
}

export interface LoginResponse {
  access_token: string
  token_type: 'bearer'
  user: UserSummary
}

export interface RegisterRequest {
  username: string
  email: string
  password: string
}

export interface LoginRequest {
  identifier: string
  password: string
}

export type ApplicationStatus = 'PENDING' | 'APPROVED' | 'REJECTED'
export type ApplicationDecision = 'APPROVE' | 'REJECT'
export interface Page<T> { items: T[]; limit: number; offset: number; total: number }
export interface MerchantProfile { id: string; name: string }
export interface Shop { id: string; merchant_id: string; name: string; owner_user_id: string; created_at: string }
export interface ShopApplication {
  id: string; merchant_id: string; applicant_user_id: string; shop_name: string; status: ApplicationStatus
  reviewer_user_id: string | null; rejection_reason: string | null; created_shop_id: string | null
  created_at: string; reviewed_at: string | null
}
export interface WarehouseAccessApplication {
  id: string; shop_id: string; applicant_user_id: string; status: ApplicationStatus
  reviewer_user_id: string | null; rejection_reason: string | null; created_at: string; reviewed_at: string | null
}
export interface OrderItem {
  id: string; sku_id: string; quantity: string; unit_price: string; subtotal: string
  sku_code_snapshot: string; spu_name_snapshot: string; specification_snapshot: string
}
export interface Order {
  id: string; merchant_id: string; shop_id: string; created_by_user_id: string; status: 'PENDING' | 'COMPLETED' | 'CANCELLED'
  total_amount: string; created_at: string; completed_at: string | null; cancelled_at: string | null; items: OrderItem[]
}

export type SpuStatus = 'OFF_SALE' | 'ON_SALE'
export interface Category { id: string; merchant_id: string; name: string; created_at: string }
export interface Spu {
  id: string; merchant_id: string; category_id: string; name: string; description: string | null
  status: SpuStatus; created_at: string; updated_at: string
}
export interface Sku {
  id: string; spu_id: string; sku_code: string; specification: string; price: string
  created_at: string; updated_at: string
}
export interface SpuDetail extends Spu { skus: Sku[] }
export type OffshelfStatus = 'PENDING' | 'APPROVED' | 'REJECTED'
export interface OffshelfApplication {
  id: string; spu_id: string; applicant_user_id: string; status: OffshelfStatus
  reviewer_user_id: string | null; rejection_reason: string | null; created_at: string; reviewed_at: string | null
  spu: { id: string; name: string; status: SpuStatus; merchant_id: string }
}
export interface Warehouse { id: string; merchant_id: string; name: string; created_at: string }
export interface Stock {
  sku_id: string; sku_code: string; specification: string; spu_id: string; spu_name: string
  warehouse_id: string; quantity: string
}
export interface StockMovement {
  movement_id: string; sku_id: string; quantity_before: string; quantity_delta: string; quantity_after: string
  idempotency_key: string | null; merchant_id: string; warehouse_id: string; actor_user_id: string
  operation_type: string; order_item_id: string | null; created_at: string
}
