"""Read-only data-quality dashboard built from the existing financial integrity audit."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.services.integrity_audit import inspect_integrity
from app.services.ledger import Ledger


def data_quality_dashboard(service: Ledger, *, limit: int = 100) -> dict[str, Any]:
    """Summarize findings without editing transactions, balances or recurrence rules."""
    audit = inspect_integrity(service, limit=limit)
    summary = audit["summary"]
    balance_count = summary["balance_mismatches"]
    duplicate_count = summary["potential_duplicate_groups"]
    recurrence_count = summary["recurrence_account_mismatches"]

    status = (
        "CRITICAL" if balance_count else ("REVIEW" if duplicate_count or recurrence_count else "OK")
    )
    sections = [
        {
            "id": "balances",
            "title": "Conciliação de saldos",
            "severity": "CRITICAL" if balance_count else "OK",
            "count": balance_count,
            "checked": summary["accounts_checked"],
            "description": "Diferenças entre saldos registrados e saldos calculados.",
            "items": [row for row in audit["balances"] if not row["matches"]],
            "truncated": False,
        },
        {
            "id": "potential_duplicates",
            "title": "Possíveis duplicidades",
            "severity": "REVIEW" if duplicate_count else "OK",
            "count": duplicate_count,
            "description": "Lançamentos semelhantes exigem confirmação manual.",
            "items": audit["potential_duplicates"],
            "truncated": audit["truncated"]["potential_duplicates"],
        },
        {
            "id": "recurrence_accounts",
            "title": "Contas de recorrências pendentes",
            "severity": "REVIEW" if recurrence_count else "OK",
            "count": recurrence_count,
            "description": "Ocorrências pendentes em conta diferente da regra atual.",
            "items": audit["recurrence_account_mismatches"],
            "truncated": audit["truncated"]["recurrence_account_mismatches"],
        },
    ]
    return {
        "read_only": True,
        "status": status,
        "summary": {
            "accounts_checked": summary["accounts_checked"],
            "balance_mismatches": balance_count,
            "potential_duplicate_groups": duplicate_count,
            "recurrence_account_mismatches": recurrence_count,
            "total_findings": balance_count + duplicate_count + recurrence_count,
        },
        "sections": sections,
        "disclaimer": (
            "Alertas são diagnósticos, não confirmação de erro ou autorização de correção. "
            "Nenhum lançamento é alterado."
        ),
    }


def data_quality_router(dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    Service = Annotated[Ledger, Depends(dependency)]

    @router.get("/dashboard/data-quality")
    def dashboard(
        service: Service,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return data_quality_dashboard(service, limit=limit)

    return router
