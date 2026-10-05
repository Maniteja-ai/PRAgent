# Retrieval dataset review packet

Status: assistant-authored draft. No human approvals or measured retrieval scores. All cases are development cases.

Review each question against the cited passage, inspect its relevance grades, and record approval or corrections in `queue.jsonl`. Grade 2 supports a required fact, grade 1 is context only, and grade 0 does not support the requested fact. All 30 candidates are explicitly graded for every vector query.

## Vector retrieval cases

| ID | Question | Direct evidence | Required facts |
| --- | --- | --- | --- |
| V01 | What must be present before a shipping voucher can be applied? | P16, P23 | Shippable products, a shipping address and an assigned shipping method are required. |
| V02 | I entered my address but have not picked delivery. Can I redeem a postage discount? | P16, P23 | An assigned shipping method is also required, alongside shippable products and an address. |
| V03 | Does shipping count toward the minimum spend for a voucher? | P19 | Eligibility uses checkout subtotal; the example with subtotal 95.96 and shipping 10 fails a 100 threshold. |
| V04 | Does minimum item quantity mean distinct products or the total number of units? | P20 | It counts total item quantity, not unique products. |
| V05 | With applyOncePerOrder enabled, is the discount applied to the whole line? | P18 | It affects one unit of the cheapest eligible item, not the entire line. |
| V06 | What does an empty shipping-voucher country list mean? | P21 | The voucher is valid for all countries if the country restriction list is empty. |
| V07 | How do vouchers and gift cards differ in price target and channel scope? | P17 | Vouchers discount subtotal, unit price or shipping and are channel scoped; gift cards reduce total and work across channels sharing currency. |
| V08 | Is a voucher applied before or after a catalogue promotion? | P26 | The voucher applies to the price after the promotion discount. |
| V09 | If deleting a cart line invalidates and removes the shipping method, what happens to its shipping voucher? | P24 | The shipping voucher is automatically unassigned when the shipping method is removed. |
| V10 | If a removed line drops subtotal below voucher minimum spend, must the shipping method also disappear? | P25 | The voucher is removed but the shipping method remains assigned in this documented scenario. |
| V11 | Can the channel of an existing checkout be changed? | P08 | The channel cannot be changed after checkout creation. |
| V12 | Do cart and checkout use separate object types in Saleor? | P06 | They use the same object type. |
| V13 | What happens when a checkout line quantity reaches zero? | P10 | The line is automatically removed; remaining lines should have quantity at least one. |
| V14 | How does checkout creation decide whether a checkout belongs to a signed-in user? | P09 | A valid auth token links the checkout to the user; without it checkout is anonymous. |
| V15 | Which mutation links a guest checkout to a user after login? | P09 | checkoutCustomerAttach converts the anonymous checkout and preserves its items. |
| V16 | Can prices and stock be assigned directly to a product without a variant? | P04 | Prices and stock are assigned at variant level; use a single variant if there are no variant options. |
| V17 | Can a product that has no variants itself appear in a checkout line? | P04 | Products may exist without variants but checkout and orders can only contain variants. |
| V18 | In the T-shirt example, which attribute is numeric and not selected by customers? | P05 | Length is numeric, variant specific and not selected by customers. |
| V19 | Which checkout field says whether shipping is required, and which items need it? | P11 | isShippingRequired indicates the requirement; the shipping step applies to physical products requiring shipping. |
| V20 | Does changing the account default alone apply it to the ongoing checkout? | P12 | Set the account default, then update shipping and/or billing address on the checkout. |
| V21 | After setting a shipping address, how do I fetch available delivery methods? | P13 | Call deliveryOptionsCalculate. |
| V22 | Which mutation selects a delivery method, and what two IDs does it require? | P14 | checkoutDeliveryMethodUpdate requires checkout id and deliveryMethodId (shipping method or warehouse ID). |
| V23 | Can I omit the country code when address validation is disabled? | P15 | Country code remains mandatory regardless of address validation rules. |
| V24 | Which mutation adds a voucher and which current result field exposes errors? | P27 | checkoutAddPromoCode adds vouchers or gift cards; errors is the current field and checkoutErrors is deprecated. |
| V25 | Which mutation removes a voucher, and where are its errors returned? | P29 | checkoutRemovePromoCode removes vouchers or gift cards and returns errors. |
| V26 | Can a promo code be removed using its ID rather than the code string? | P29, P30 | checkoutRemovePromoCode accepts promoCodeId as the gift card or voucher ID. |
| V27 | For adding a promo code, what replaces deprecated checkoutId and token arguments? | P28 | Use id instead of checkoutId and token. |
| V28 | Does the storefront README explicitly advertise changing cart quantities? | P03 | The README lists a cart drawer with real-time updates and quantity editing. |
| V29 | Does the README describe selecting multiple variant attributes such as color and size? | P02 | The README explicitly describes multi-attribute variant selection. |
| V30 | Which available checkout objects can depend on the selected channel? | P07 | Products/variants, payment gateways, shipping methods, collection points and discounts depend on channel. |
| V31 | How are minimum-spend eligibility and minimum-item eligibility calculated? | P19, P20 | Minimum spend uses the channel-defined checkout subtotal threshold.; Minimum items uses total quantity rather than distinct products. |
| V32 | How do I assign delivery and what must exist to apply a shipping voucher? | P14, P16, P23 | Assign delivery using checkoutDeliveryMethodUpdate with checkout ID and delivery-method ID.; Shipping vouchers require shippable products, shipping address and assigned method. |
| V33 | Is promoCode required for both add-promo and remove-promo GraphQL mutations? | P27, P29 | The add mutation declares promoCode as String!, so required.; The remove mutation declares promoCode as String and supports promoCodeId. |
| V34 | What is the current discount percentage configured for TSIGMA10 in our backend? | None | Not answerable from this bounded corpus; do not use outside knowledge. |
| V35 | What phone number should a shopper call for this store support? | None | Not answerable from this bounded corpus; do not use outside knowledge. |
| V36 | What is the exact current DOM selector of the deployed Remove discount code button? | None | Not answerable from this bounded corpus; do not use outside knowledge. |
| V37 | Exactly how many checkoutAddPromoCode requests per minute does this backend allow? | None | Not answerable from this bounded corpus; do not use outside knowledge. |
| V38 | How many days after delivery does this store permit product returns? | None | Not answerable from this bounded corpus; do not use outside knowledge. |
| V39 | Since shipping vouchers work without a shipping address, how can I apply one to an address-free checkout? | P16, P23 | The premise is false for the documented shipping voucher: a shipping address is required. |
| V40 | what do i need for a shiping cupon to work? | P16, P23 | Shippable items, shipping address and assigned shipping method. |

## Passage evidence

### P01 storefront-readme

[Frozen source](sources/storefront-readme.md) lines 38 to 47; source version `23bc49ccd22e13e182b30daff562d5e5c9af874c`.

````text
### 🛒 Open Checkout

The checkout is where most storefronts fall apart or fall short. Paper's doesn't. We aim to provide open UI components and full wiring around the whole process.

- **Multi-step, mobile-first** — Each step is a focused form. No infinite scrolling on phones.
- **Guest & authenticated** — Seamless flow for everyone. Logged-in users get address book and saved preferences.
- **International address forms** — Country-aware fields that adapt (US states, UK postcodes, German formats).
- **Connection resilience** — Automatic retries with exponential backoff. Flaky networks? Handled.
- **Componentized architecture** — Swap steps, add steps, remove steps. It's your checkout.
- **Multi-channel ready** — Different currencies and shipping zones per channel.
````

### P02 storefront-readme

[Frozen source](sources/storefront-readme.md) lines 49 to 59; source version `23bc49ccd22e13e182b30daff562d5e5c9af874c`.

````text
### 🌍 Multi-Channel, Multi-Currency

One codebase, many storefronts. Channel-scoped routing means `/us/products` and `/eu/products` can serve different catalogs, prices, and shipping options—all from the same deployment.

### 📱 Product Pages Done Right

The hard parts are solved. Adapt the look, keep the logic.

- **Multi-attribute variant selection** — Color + Size + Material? Handled. Complex variant matrices just work.
- **Dynamic pricing** — Sale prices, variant-specific pricing, channel pricing—all reactive.
- **Image gallery** — Next.js Image optimization, proper aspect ratios, keyboard navigation.
````

### P03 storefront-readme

[Frozen source](sources/storefront-readme.md) lines 86 to 99; source version `23bc49ccd22e13e182b30daff562d5e5c9af874c`.

````text
## What's in the Box

| Feature              | Description                                                                       |
| -------------------- | --------------------------------------------------------------------------------- |
| **Checkout**         | Multi-step flow with guest/auth support, address selector, international forms    |
| **Cart**             | Slide-over drawer with real-time updates, quantity editing                        |
| **Product Pages**    | Multi-attribute variants, image gallery, sticky add-to-cart                       |
| **Product Listings** | Category & collection pages with pagination                                       |
| **Navigation**       | Dynamic menus from Saleor, mobile hamburger                                       |
| **SEO**              | Metadata, JSON-LD, Open Graph images                                              |
| **Caching**          | ISR with on-demand revalidation via webhooks                                      |
| **Customer Profile** | Account dashboard, address book, order history, password change, account deletion |
| **Authentication**   | Login, register, password reset, guest checkout                                   |
| **API Resilience**   | Automatic retries, rate limiting, timeouts—handles flaky connections gracefully   |
````

### P04 products

[Frozen source](sources/products.md) lines 9 to 15; source version `current-online-unpinned`.

````text
## Product and variants[​](#product-and-variants "Direct link to Product and variants")

Variants are designed to provide variations of the product, such as size, material, color, or any other dimension that can be user-defined; if the product has no variants, you will need to create a product with a single variant because prices and stock can only be assigned at a variant level.

It might seem like it is overhead to configure twice the availability of variant and product (especially when there is only one variant), but it has an intentional purpose. First, it gives more degree of freedom per variant, and second, it is convenient when you have many variants and need to disable one all at once in a single channel.

It is possible to create products without variants, but checkout and orders can only contain variants.
````

### P05 products

[Frozen source](sources/products.md) lines 21 to 29; source version `current-online-unpinned`.

````text
## Product types[​](#product-types "Direct link to Product types")

Product type is a collection of [attributes](/developer/attributes/overview) with different types of fields. For example a *Product Type* **T-shirt** will have **Size**, **Material** and **Length** attributes.

**Material:** can be *Cotton* or *Polyester* and represents a dropdown attribute assigned to a product type.

**Size:** of the shirt is a product option that can be selected by the customer; it comes with predefined sizes so that it will be of type *Dropdown* and *Variant selection attribute*.

**Length:** is an attribute specific to a variant. It is not something that customers can select, and it is unique to each product so that it will be of type *Numeric*.
````

### P06 checkout

[Frozen source](sources/checkout.md) lines 3 to 6; source version `current-online-unpinned`.

````text
### Why Is There No Cart Model?[​](#why-is-there-no-cart-model "Direct link to Why Is There No Cart Model?")

Saleor has no distinct object type for shopping carts and checkouts. We wanted the same features – like discounts, vouchers, address-specific taxes, and shipping estimates – to be available in the cart and the checkout, so we've decided to use the same object type for both.
Checkout provides the interface for standard cart operations like adding products or promo codes. It can also be processed in almost any order, for example, by saving a billing address before adding any items.
````

### P07 checkout

[Frozen source](sources/checkout.md) lines 19 to 29; source version `current-online-unpinned`.

````text
## Multiple Channels and Checkout[​](#multiple-channels-and-checkout "Direct link to Multiple Channels and Checkout")

Depending on the chosen channel, the user will have access to different objects. This impacts available:

* Products and Product Variants
* Payment Gateways
* Shipping Methods
* Collection Points
* Discounts

[Learn more about using multiple channels](/developer/channels/overview).
````

### P08 checkout-lifecycle

[Frozen source](sources/checkout-lifecycle.md) lines 3 to 7; source version `current-online-unpinned`.

````text
## Checkout Creation[​](#checkout-creation "Direct link to Checkout Creation")

Upon creation, `checkout` is assigned to a channel that influences product stock, availability, and price. The channel can not be changed after the checkout is created. Learn more about [Channels](/developer/channels/overview).

Checkout can be created from [existing orders](/developer/order/order-to-checkout), which is helpful for re-order functionality.
````

### P09 checkout-lifecycle

[Frozen source](sources/checkout-lifecycle.md) lines 9 to 20; source version `current-online-unpinned`.

````text
## Authentication in Checkout[​](#authentication-in-checkout "Direct link to Authentication in Checkout")

Saleor allows you to manage both logged-in customers and guest users using the same API mutations.
You do not need separate logic branches for different user states; instead, Saleor determines the checkout ownership based on the request's authentication context.

* **With Auth Header:** If the checkout creation request includes a valid user token, the new checkout is automatically "Signed" (linked to that specific User ID).
* **Without Auth Header:** The checkout is created as "Anonymous" (guest). It is identified solely by a unique token and is not linked to any user profile.

### Attaching a Guest Checkout to a User[​](#attaching-a-guest-checkout-to-a-user "Direct link to Attaching a Guest Checkout to a User")

If a guest user logs in midway through their shopping experience, you can convert their anonymous checkout into a signed one using the [`checkoutCustomerAttach`](/api-reference/checkout/mutations/checkout-customer-attach) mutation.
This ensures their items are preserved and linked to their account history.
````

### P10 checkout-lifecycle

[Frozen source](sources/checkout-lifecycle.md) lines 22 to 24; source version `current-online-unpinned`.

````text
## Updating Checkout[​](#updating-checkout "Direct link to Updating Checkout")

* **Updating lines**. Each line (product variant) should have at least a quantity of 1; if the line reaches a quantity of 0, it will be automatically removed. Optionally [Price overwrites](/developer/checkout/api-guide#setting-custom-line-prices) and [Line Stacking](/developer/checkout/api-guide#creating-two-lines-using-a-single-variant) can be controlled via API.
````

### P11 shipping-address

[Frozen source](sources/shipping-address.md) lines 3 to 5; source version `current-online-unpinned`.

````text
## Shipping[​](#shipping "Direct link to Shipping")

This step is only used if purchased items require shipping (if they are physical products). The user must select a specific shipping method to create shipping for this checkout. To signify whether shipping is required, use the `isShippingRequired` field in the [`Checkout`](/api-reference/checkout/objects/checkout) object.
````

### P12 shipping-address

[Frozen source](sources/shipping-address.md) lines 88 to 100; source version `current-online-unpinned`.

````text
### Default Address[​](#default-address "Direct link to Default Address")

Customers with accounts can set up default addresses, which will be attached automatically during the checkout creation. More information on API reference page for [accountSetDefaultAddress](/api-reference/users/mutations/account-set-default-address).

#### Changing the Default Address During Checkout[​](#changing-the-default-address-during-checkout "Direct link to Changing the Default Address During Checkout")

To change your default address during checkout:

1. Select a previously saved address or create a new one.
2. Use the [accountSetDefaultAddress](/api-reference/users/mutations/account-set-default-address) mutation to update your default shipping or billing address.
3. Call [`checkoutShippingAddressUpdate`](/api-reference/checkout/mutations/checkout-shipping-address-update) and/or [`checkoutBillingAddressUpdate`](/api-reference/checkout/mutations/checkout-billing-address-update) to apply the selected address to the current checkout.

This process ensures that the address you want to use is both set as your account default and applied to the ongoing checkout.
````

### P13 shipping-address

[Frozen source](sources/shipping-address.md) lines 104 to 106; source version `current-online-unpinned`.

````text
#### Shipping Methods[​](#shipping-methods "Direct link to Shipping Methods")

After choosing the shipping address, use the [`deliveryOptionsCalculate`](/api-reference/shipping/mutations/delivery-options-calculate) mutation to explicitly fetch available delivery methods.
````

### P14 shipping-address

[Frozen source](sources/shipping-address.md) lines 225 to 232; source version `current-online-unpinned`.

````text
### Selecting the Delivery Method[​](#selecting-the-delivery-method "Direct link to Selecting the Delivery Method")

Use the [`checkoutDeliveryMethodUpdate`](/api-reference/checkout/mutations/checkout-delivery-method-update) mutation to effectively pair the specific [`Checkout`](/api-reference/checkout/objects/checkout) object with the specified delivery method selected by the user.

This operation requires the following input:

* `id`: the checkout ID (the `id` field of the [`Checkout`](/api-reference/checkout/objects/checkout) object).
* `deliveryMethodId`: the shipping method ID or Warehouse ID (`delivery` from the `deliveryOptionsCalculate` mutation or `availableCollectionPoints` field of the [`Checkout`](/api-reference/checkout/objects/checkout) object).
````

### P15 shipping-address

[Frozen source](sources/shipping-address.md) lines 369 to 377; source version `current-online-unpinned`.

````text
## Address Validation[​](#address-validation "Direct link to Address Validation")

The checkout's mutations that accept an address as an input have a field that can turn off the address validation. It allows assigning a partial or not fully valid address to the checkout. Providing country code is mandatory for all addresses regardless of the rules provided in this input.

The address [validation](/api-reference/checkout/inputs/checkout-address-validation-rules) input has two boolean fields:

* `checkRequiredFields` - signals Saleor to raise an error when the provided address doesn't have all the required fields. Set to `true` by default.
* `checkFieldsFormat` - signals Saleor to raise an error when the provided address doesn't match the expected format. Set to `true` by default.
* `enableFieldsNormalization` - determines if Saleor should apply normalization on address fields. Example: converting city field to uppercase letters. Set to `true` by default.
````

### P16 vouchers

[Frozen source](sources/vouchers.md) lines 12 to 23; source version `current-online-unpinned`.

````text
## Types[​](#types "Direct link to Types")

Vouchers come in three types:

* `ENTIRE_ORDER`: Applies discount to the checkout subtotal
* `SPECIFIC_PRODUCT`: Applies discount to eligible product unit prices. You define eligibility by linking products, variants, categories, or collections.
* `SHIPPING`: Applies discount to shipping costs. To apply a shipping voucher, the checkout must include shippable products, shipping address, and a shipping method assigned.

Vouchers also have two value types:

* `FIXED`: Reduces price by a specific amount
* `PERCENTAGE`: Reduces price by a specific percentage
````

### P17 vouchers

[Frozen source](sources/vouchers.md) lines 25 to 30; source version `current-online-unpinned`.

````text
note

**Vouchers vs Gift Cards**

Vouchers discount the subtotal, unit price, or shipping based on their type and are scoped to channels.
Gift cards reduce the total price and are currency-based, usable across channels with the same currency.
````

### P18 vouchers

[Frozen source](sources/vouchers.md) lines 34 to 40; source version `current-online-unpinned`.

````text
### Apply Once Per Order[​](#apply-once-per-order "Direct link to Apply Once Per Order")

When the `Voucher.applyOncePerOrder` setting is enabled, the discount is applied only once per order, targeting the cheapest item.
The discount affects a single unit, not the entire order line.

* For **product-specific vouchers**: applies to the cheapest item included in the discount.
* For **entire order vouchers**: applies to the cheapest item overall.
````

### P19 vouchers

[Frozen source](sources/vouchers.md) lines 42 to 49; source version `current-online-unpinned`.

````text
### Minimal Order Value[​](#minimal-order-value "Direct link to Minimal Order Value")

Specifies the minimum checkout subtotal required for the voucher to be applicable.
The value is defined per sales channel via `VoucherChannelListing.minSpent`.

If the subtotal does not meet this threshold, the voucher will not be applied.

**Example:** If `minSpent` is set to 100.00, and the subtotal is 95.96 with 10.00 shipping (total 105.96), the voucher will not be applied.
````

### P20 vouchers

[Frozen source](sources/vouchers.md) lines 51 to 54; source version `current-online-unpinned`.

````text
### Minimal Items Quantity[​](#minimal-items-quantity "Direct link to Minimal Items Quantity")

`Voucher.minCheckoutItemsQuantity` specifies the minimum number of products in the cart required to redeem the voucher.
This is based on the total quantity of items, not unique products.
````

### P21 vouchers

[Frozen source](sources/vouchers.md) lines 56 to 71; source version `current-online-unpinned`.

````text
### Limit Shipping Vouchers to Specific Countries[​](#limit-shipping-vouchers-to-specific-countries "Direct link to Limit Shipping Vouchers to Specific Countries")

`Voucher.countries` defines a list of countries where the shipping voucher can be applied.

* If set, the voucher will only be valid for orders shipping to the specified countries.
* If left empty, the voucher will be valid for all countries.

**Example:**

```
{  
  "countries": ["US", "CA", "GB"]  
}
```

In this example, the voucher is only valid for shipments to the United States, Canada, and the United Kingdom.
````

### P22 apply-vouchers

[Frozen source](sources/apply-vouchers.md) lines 9 to 14; source version `current-online-unpinned`.

````text
## Apply a Voucher Code[​](#apply-a-voucher-code "Direct link to Apply a Voucher Code")

To apply the voucher on checkout use [`checkoutAddPromoCode`](/api-reference/checkout/mutations/checkout-add-promo-code)
mutation. The discount will be visible both in the line prices and in the `checkout.discount` field.
To apply the voucher on draft order use [`draftOrderCreate`](/api-reference/orders/mutations/draft-order-create) or [`draftOrderUpdate`](/api-reference/orders/mutations/draft-order-update)
and pass `voucherCode` as an argument.
````

### P23 apply-vouchers

[Frozen source](sources/apply-vouchers.md) lines 508 to 513; source version `current-online-unpinned`.

````text
### Shipping Voucher[​](#shipping-voucher "Direct link to Shipping Voucher")

To apply a shipping voucher, the checkout must include shippable products, a provided shipping address, and a shipping method assigned.

In this example the checkout consists of one line for **$100**, shipping price for **$20** and **50%** shipping voucher.
The discount affects shipping price only.
````

### P24 apply-vouchers

[Frozen source](sources/apply-vouchers.md) lines 597 to 607; source version `current-online-unpinned`.

````text
### Shipping method removal invalidates shipping voucher[​](#shipping-method-removal-invalidates-shipping-voucher "Direct link to Shipping method removal invalidates shipping voucher")

**Scenario:**

1. Checkout has 2 lines and a shipping method assigned.
2. A shipping voucher is applied.
3. One checkout line is removed.
4. The assigned shipping method is no longer valid for the updated checkout (e.g., weight, location, or product eligibility changes).
5. The shipping method is automatically removed from the checkout.

Since the shipping method is removed, the shipping voucher is also automatically unassigned.
````

### P25 apply-vouchers

[Frozen source](sources/apply-vouchers.md) lines 665 to 674; source version `current-online-unpinned`.

````text
### Subtotal change invalidates minimum spent voucher[​](#subtotal-change-invalidates-minimum-spent-voucher "Direct link to Subtotal change invalidates minimum spent voucher")

**Scenario:**

1. Checkout has 2 lines and a valid shipping method assigned.
2. A voucher with a minimum spent amount condition is applied.
3. One line is removed from the checkout.
4. The checkout subtotal drops below the voucher’s minimum requirement.

The voucher is automatically removed, but the shipping method remains assigned.
````

### P26 apply-vouchers

[Frozen source](sources/apply-vouchers.md) lines 732 to 737; source version `current-online-unpinned`.

````text
## Combining Promotions and Vouchers[​](#combining-promotions-and-vouchers "Direct link to Combining Promotions and Vouchers")

Catalogue promotions and vouchers can be combined. In this case, the voucher discount
is applied to the price after the promotion discount.
Let's consider an example: the checkout has two items, and the first item is on **$5** fixed promotion.
A percentage discount of **50%** is being applied to the entire order.
````

### P27 add-promo-api

[Frozen source](sources/add-promo-api.md) lines 3 to 26; source version `current-online-unpinned`.

````text
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
````

### P28 add-promo-api

[Frozen source](sources/add-promo-api.md) lines 30 to 54; source version `current-online-unpinned`.

````text
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
````

### P29 remove-promo-api

[Frozen source](sources/remove-promo-api.md) lines 3 to 26; source version `current-online-unpinned`.

````text
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
````

### P30 remove-promo-api

[Frozen source](sources/remove-promo-api.md) lines 31 to 59; source version `current-online-unpinned`.

````text
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
````

## Graph retrieval cases

| ID | Scenario | Changed symbols | Expected status | Expected flows |
| --- | --- | --- | --- | --- |
| G01 | direct-ui-mapping | summary | OK | flow-apply, flow-remove, flow-quantity |
| G02 | reverse-dependency-direction | discount | OK | flow-apply, flow-remove, flow-quantity |
| G03 | transitive-dependency | subtotal | OK | flow-apply, flow-remove, flow-quantity |
| G04 | duplicate-paths | discount | OK | flow-apply, flow-remove, flow-quantity |
| G05 | cycle-terminates | summary | OK | flow-apply, flow-remove, flow-quantity |
| G06 | no-ui-mapping | orphan | UNMAPPED | None |
| G07 | unknown-symbol | missing-symbol | SYMBOL_NOT_FOUND | None |
| G08 | homonym-does-not-inherit-links | homonym | UNMAPPED | None |
| G09 | ambiguous-name-only | Name lookup or empty | AMBIGUOUS_SYMBOL | None |
| G10 | do-not-traverse-into-callees | caller | UNMAPPED | None |
| G11 | one-hop-limit | subtotal | UNMAPPED_WITHIN_BUDGET | None |
| G12 | two-hop-sufficient | subtotal | OK | flow-apply, flow-remove, flow-quantity |
| G13 | reject-cross-project | discount | OK | flow-apply, flow-remove, flow-quantity |
| G14 | reject-cross-revision | discount | OK | flow-apply, flow-remove, flow-quantity |
| G15 | exclude-proposed-and-rejected-links | summary | OK | flow-apply, flow-remove, flow-quantity |
| G16 | independent-login-change | login-code | OK | flow-login |
| G17 | multiple-changed-symbols | discount, login-code | OK | flow-apply, flow-remove, flow-quantity, flow-login |
| G18 | empty-diff | Name lookup or empty | NO_CHANGES | None |
| G19 | missing-scope | discount | INVALID_INPUT | None |
| G20 | no-selected-version | discount | SYMBOL_NOT_FOUND | None |

Every graph result is defined by the synthetic fixture and traversal contract in the dataset README. It is not evidence of real Saleor browser behavior. Inspect code evidence seeds separately before approving future real impact labels.
