-- ============================================================
-- Table d'exclusion pour la page "🏪 Revendeurs Wizishop"
-- Même principe que skus_ignores / faire_ignores / ankorstore_ignores :
-- un code promo listé ici est exclu du tableau "Revendeurs Wizishop"
-- jusqu'à réintégration manuelle.
-- ============================================================

CREATE TABLE IF NOT EXISTS revendeurs_ignores (
    code_promo TEXT PRIMARY KEY,
    raison     TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
