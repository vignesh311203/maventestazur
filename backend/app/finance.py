"""Public, stateless estimates. No bank underwriting, tax assessment or customer data storage."""
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING, localcontext
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

router = APIRouter(prefix="/api/finance", tags=["finance"])
D = Decimal
CENT = D("0.01")
HUNDRED = D(100)

def money(n):
    return D(n).quantize(CENT, rounding=ROUND_HALF_UP)

def pct(amount, percent):
    return money(amount * percent / HUNDRED)

class PurchaseIn(BaseModel):
    area_sqft: Decimal = Field(gt=0, le=1_000_000, decimal_places=2)
    rate_per_sqft: Decimal = Field(gt=0, le=1_000_000, decimal_places=2)
    corner_plot: bool | None = None
    corner_premium_percent: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4)
    guidance_value: Decimal | None = Field(default=None, gt=0, le=10**12, decimal_places=2)
    stamp_percent: Decimal = Field(default=D("5"), ge=0, le=100, decimal_places=4)
    registration_percent: Decimal = Field(default=D("2"), ge=0, le=100, decimal_places=4)
    cess_on_duty_percent: Decimal = Field(default=D("10"), ge=0, le=100, decimal_places=4)
    surcharge_on_duty_percent: Decimal = Field(default=D("2"), ge=0, le=100, decimal_places=4)
    govt_other: Decimal = Field(default=D(0), ge=0, le=10**9, decimal_places=2)
    legal_charges: Decimal = Field(default=D(0), ge=0, le=10**9, decimal_places=2)
    developer_charges: Decimal = Field(default=D(0), ge=0, le=10**9, decimal_places=2)
    seller_resident_india: bool | None = None
    rural_agricultural_land: bool = False

    @model_validator(mode="after")
    def corner_rule(self):
        if self.corner_plot is not True and self.corner_premium_percent not in (None, D(0)):
            raise ValueError("A corner premium requires a confirmed corner plot")
        return self

@router.post("/purchase")
def purchase(data: PurchaseIn):
    base = money(data.area_sqft * data.rate_per_sqft)
    premium = pct(base, data.corner_premium_percent or D(0)) if data.corner_plot else D(0)
    sale = money(base + premium)
    basis = max(sale, data.guidance_value or sale)
    stamp = pct(basis, data.stamp_percent)
    # Karnataka ordinary conveyance registration fee: each Rs 1,000 or part.
    # The percentage is editable; this rounding convention remains explicit.
    reg_basis = (basis / D(1000)).to_integral_value(rounding=ROUND_CEILING) * D(1000)
    registration = pct(reg_basis, data.registration_percent)
    cess = pct(stamp, data.cess_on_duty_percent)
    surcharge = pct(stamp, data.surcharge_on_duty_percent)
    government = money(stamp + registration + cess + surcharge + data.govt_other)
    threshold = D(5_000_000)
    tds_review_required = max(sale, basis) >= threshold and data.seller_resident_india is None and not data.rural_agricultural_land
    tds_applicable = max(sale, basis) >= threshold and data.seller_resident_india is True and not data.rural_agricultural_land
    tds_basis = max(sale, basis)
    tds_withheld = pct(tds_basis, D(1)) if tds_applicable else D(0)
    return {
        "base_price": base, "corner_premium": premium, "sale_price": sale,
        "guidance_value": data.guidance_value, "assessment_basis": basis,
        "registration_basis": reg_basis, "stamp_duty": stamp, "registration_fee": registration,
        "cess": cess, "surcharge": surcharge, "other_government": data.govt_other,
        "government_total": government, "legal_charges": data.legal_charges,
        "developer_charges": data.developer_charges,
        "purchase_total": money(sale + government + data.legal_charges + data.developer_charges),
        "tds_applicable": tds_applicable,
        "tds_review_required": tds_review_required,
        "tds_withheld_from_seller": tds_withheld,
        "indicative_tds_if_resident": pct(tds_basis, D(1)) if tds_review_required else D(0),
        "seller_payment_after_tds": money(sale - tds_withheld) if tds_applicable else None,
        "provisional": data.guidance_value is None or data.corner_plot is None or (data.corner_plot and data.corner_premium_percent is None),
        "notice": "Illustrative Karnataka ordinary sale deed estimate. Confirm assessed value, deed, local surcharge, concessions and exact payable charges in Kaveri. Guidance value is a valuation, not an additional payment. Buyer TDS, if applicable, is withheld from the seller payment and deposited with the tax authority, not added on top of the sale price. Seller residence and land classification need verification. No tax or bank approval is implied."
    }

class ConstructionIn(BaseModel):
    built_area_sqft: Decimal = Field(gt=0, le=1_000_000, decimal_places=2)
    build_rate_per_sqft: Decimal = Field(gt=0, le=1_000_000, decimal_places=2)
    contingency_percent: Decimal = Field(default=D(0), ge=0, le=100, decimal_places=4)
    professional_fees: Decimal = Field(default=D(0), ge=0, le=10**9, decimal_places=2)
    approval_fees: Decimal = Field(default=D(0), ge=0, le=10**9, decimal_places=2)
    other_costs: Decimal = Field(default=D(0), ge=0, le=10**9, decimal_places=2)

@router.post("/construction")
def construction(data: ConstructionIn):
    base = money(data.built_area_sqft * data.build_rate_per_sqft)
    contingency = pct(base, data.contingency_percent)
    return {"base_construction": base, "contingency": contingency,
            "professional_fees": data.professional_fees, "approval_fees": data.approval_fees,
            "other_costs": data.other_costs,
            "construction_total": money(base + contingency + data.professional_fees + data.approval_fees + data.other_costs)}

class LoanIn(BaseModel):
    loan_type: Literal["plot", "construction"]
    project_cost: Decimal = Field(gt=0, le=10**12, decimal_places=2)
    loan_amount: Decimal = Field(ge=0, le=10**12, decimal_places=2)
    annual_rate_percent: Decimal = Field(ge=0, le=100, decimal_places=4)
    months: int = Field(ge=1, le=600)
    upfront_fees: Decimal = Field(default=D(0), ge=0, le=10**9, decimal_places=2)
    disbursed_amount: Decimal | None = Field(default=None, ge=0, le=10**12, decimal_places=2)

    @model_validator(mode="after")
    def validate_amount(self):
        if self.loan_amount > self.project_cost:
            raise ValueError("Loan amount cannot exceed the entered project cost")
        if self.disbursed_amount is not None and self.disbursed_amount > self.loan_amount:
            raise ValueError("Disbursed amount cannot exceed the loan amount")
        if self.loan_type == "plot" and self.disbursed_amount is not None:
            raise ValueError("Stage disbursement applies to construction only")
        return self

@router.post("/loan")
def loan(data: LoanIn):
    # Monthly reducing balance, fixed nominal rate, rounded monthly interest.
    # Adjust final instalment to clear rounding residue; all totals reconcile.
    principal = money(data.loan_amount)
    r = data.annual_rate_percent / D(1200)
    n = data.months
    with localcontext() as ctx:
        ctx.prec = 42
        payment = money(principal / n if not r else principal * r / (1 - (1+r)**(-n)))
    balance = principal
    schedule = []
    interest_total = D(0)
    paid_total = D(0)
    for month in range(1, n+1):
        interest = money(balance * r)
        due = balance + interest
        instalment = due if month == n else min(due, max(payment, interest))
        principal_paid = instalment - interest
        balance = money(balance - principal_paid)
        interest_total += interest
        paid_total += instalment
        schedule.append({"month": month, "payment": instalment, "principal": principal_paid,
                         "interest": interest, "balance": balance})
    drawn = data.disbursed_amount
    return {
        "loan_type": data.loan_type, "loan_amount": principal,
        "own_contribution": money(data.project_cost - principal),
        "loan_to_cost_percent": money(principal / data.project_cost * HUNDRED),
        "monthly_emi": payment, "final_payment": schedule[-1]["payment"],
        "total_interest": money(interest_total), "total_repayment": money(paid_total),
        "upfront_fees": data.upfront_fees,
        "cash_required": money(data.project_cost - principal + data.upfront_fees),
        "total_cost_with_interest_and_fees": money(data.project_cost + interest_total + data.upfront_fees),
        "pre_emi_monthly": money(drawn * r) if drawn is not None else None,
        "schedule": schedule,
        "notice": "Fixed-rate monthly reducing-balance estimate, full loan disbursed at the start of EMI term. Floating rates, daily-interest conventions, stage disbursement, fees and bank rounding can change actual payments. Pre-EMI is interest on the entered amount already drawn; it is not included in the amortization totals."
    }
