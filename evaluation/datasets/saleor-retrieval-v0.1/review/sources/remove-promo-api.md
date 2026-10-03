[View Markdown](/api-reference/checkout/mutations/checkout-remove-promo-code.md)

Remove a gift card or a voucher from a checkout.

Triggers the following webhook events:

* CHECKOUT\_UPDATED (async): A checkout was updated.

```
checkoutRemovePromoCode(  
  checkoutId: ID  
  id: ID  
  promoCode: String  
  promoCodeId: ID  
  token: UUID  
): CheckoutRemovePromoCode
```

Details

```
type CheckoutRemovePromoCode {  
  checkout: Checkout  
  checkoutErrors: [CheckoutError!]! @deprecated  
  errors: [CheckoutError!]!  
}
```

### Arguments[​](#arguments "Direct link to Arguments")

#### [`id`](#id) ● [`ID`](/api-reference/miscellaneous/scalars/id)[​](#id "Direct link to id")

The checkout's ID.

#### [`promoCode`](#promo-code) ● [`String`](/api-reference/miscellaneous/scalars/string)[​](#promo-code "Direct link to promo-code")

Gift card code or voucher code.

#### [`promoCodeId`](#promo-code-id) ● [`ID`](/api-reference/miscellaneous/scalars/id)[​](#promo-code-id "Direct link to promo-code-id")

Gift card or voucher ID.

Show deprecatedHide deprecated

#### [`checkoutId`](#checkout-id) ● [`ID`](/api-reference/miscellaneous/scalars/id)[​](#checkout-id "Direct link to checkout-id")

DEPRECATED

Use `id` instead.

The ID of the checkout.

#### [`token`](#token) ● [`UUID`](/api-reference/miscellaneous/scalars/uuid)[​](#token "Direct link to token")

DEPRECATED

Use `id` instead.

Checkout token.

### Type[​](#type "Direct link to Type")

#### [`CheckoutRemovePromoCode`](/api-reference/checkout/objects/checkout-remove-promo-code)[​](#checkoutremovepromocode "Direct link to checkoutremovepromocode")

Remove a gift card or a voucher from a checkout.

Triggers the following webhook events:

* CHECKOUT\_UPDATED (async): A checkout was updated.

* [Arguments](#arguments)* [Type](#type)