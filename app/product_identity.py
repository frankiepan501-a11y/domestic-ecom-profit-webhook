"""Finance-facing ERP product identity helpers."""


def erp_display_name(sku: str, erp_name: str) -> str:
    """Return the ERP Chinese name without falling back to a marketplace title."""
    name = str(erp_name or "").strip()
    if name:
        return name
    normalized_sku = str(sku or "").strip()
    return f"ERP品名未维护（{normalized_sku or 'ERP SKU未映射'}）"
