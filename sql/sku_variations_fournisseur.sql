-- ============================================================
-- Table sku_variations_fournisseur
-- Référence fournisseur détaillée par SKU (variation), au-delà des champs
-- fournisseur / reference_fournisseur déjà présents sur la table produits.
-- ============================================================

CREATE TABLE IF NOT EXISTS sku_variations_fournisseur (
    sku                   TEXT        PRIMARY KEY,
    fournisseur           TEXT,
    reference_fournisseur TEXT,
    couleur_fournisseur   TEXT,
    reference_complete    TEXT        GENERATED ALWAYS AS (reference_fournisseur || ' ' || couleur_fournisseur) STORED,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE sku_variations_fournisseur ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON sku_variations_fournisseur TO anon, authenticated;

CREATE POLICY "Accès total sku_variations_fournisseur"
    ON sku_variations_fournisseur FOR ALL
    USING (true)
    WITH CHECK (true);

-- Constaté après création : contrairement aux autres tables (qui donnent un
-- accès implicite à service_role), celle-ci refusait service_role avec
-- "permission denied" tant que ce GRANT n'était pas explicite.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON sku_variations_fournisseur TO service_role;
