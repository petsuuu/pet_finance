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
    classification_count = summary.get("classification_findings", 0)
    similar_count = summary.get("similar_duplicate_pairs", 0)
    reference_count = summary.get("pending_reference_findings", 0)

    review_count = (
        duplicate_count + recurrence_count + classification_count + similar_count + reference_count
    )
    status = "CRITICAL" if balance_count else ("REVIEW" if review_count else "OK")
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
        {
            "id": "classification",
            "title": "Classificação de despesas",
            "severity": "REVIEW" if classification_count else "OK",
            "count": classification_count,
            "description": (
                "Categorias ausentes, inativas ou diferentes do padrão do estabelecimento."
            ),
            "items": audit.get("classification_findings", []),
            "truncated": audit["truncated"].get("classification_findings", False),
        },
        {
            "id": "pending_references",
            "title": "Referências de pendências",
            "severity": "REVIEW" if reference_count else "OK",
            "count": reference_count,
            "description": "Pendências com conta inativa ou recorrência ausente/arquivada.",
            "items": audit.get("pending_reference_findings", []),
            "truncated": audit["truncated"].get("pending_reference_findings", False),
        },
        {
            "id": "similar_duplicates",
            "title": "Descrições semelhantes",
            "severity": "REVIEW" if similar_count else "OK",
            "count": similar_count,
            "description": "Mesmo valor, conta e data com descrições muito semelhantes.",
            "items": audit.get("similar_duplicate_pairs", []),
            "truncated": audit["truncated"].get("similar_duplicate_pairs", False),
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
            "classification_findings": classification_count,
            "similar_duplicate_pairs": similar_count,
            "pending_reference_findings": reference_count,
            "total_findings": balance_count + review_count,
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
