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
