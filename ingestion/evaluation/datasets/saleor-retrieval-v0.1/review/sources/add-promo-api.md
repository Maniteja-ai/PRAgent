[View Markdown](/api-reference/checkout/mutations/checkout-add-promo-code.md)

Adds a gift card or a voucher to a checkout.

Triggers the following webhook events:

* CHECKOUT\_UPDATED (async): A checkout was updated.

```
checkoutAddPromoCode(  
  checkoutId: ID  
  id: ID  
  promoCode: String!  
  token: UUID  
): CheckoutAddPromoCode
```

Details

```
type CheckoutAddPromoCode {  
  checkout: Checkout  
  checkoutErrors: [CheckoutError!]! @deprecated  
  errors: [CheckoutError!]!  
}
```

### Arguments[​](#arguments "Direct link to Arguments")

#### [`id`](#id) ● [`ID`](/api-reference/miscellaneous/scalars/id)[​](#id "Direct link to id")

The checkout's ID.

#### [`promoCode`](#promo-code) ● [`String!`](/api-reference/miscellaneous/scalars/string)[​](#promo-code "Direct link to promo-code")

Gift card code or voucher code.

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

#### [`CheckoutAddPromoCode`](/api-reference/checkout/objects/checkout-add-promo-code)[​](#checkoutaddpromocode "Direct link to checkoutaddpromocode")

Adds a gift card or a voucher to a checkout.

Triggers the following webhook events:

* CHECKOUT\_UPDATED (async): A checkout was updated.

* [Arguments](#arguments)* [Type](#type)