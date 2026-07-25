-- ============================================================
-- a) Table variations_fournisseur
-- Référentiel des couleurs fournisseur, normalisé (au lieu du texte libre
-- couleur_fournisseur sur sku_variations_fournisseur).
-- ============================================================

CREATE TABLE IF NOT EXISTS variations_fournisseur (
    id                  BIGSERIAL   PRIMARY KEY,
    fournisseur         TEXT        NOT NULL,
    couleur_fournisseur TEXT        NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (fournisseur, couleur_fournisseur)
);

ALTER TABLE variations_fournisseur ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON variations_fournisseur TO anon, authenticated, service_role;

-- BIGSERIAL crée une séquence implicite (variations_fournisseur_id_seq) ;
-- le GRANT sur la table ne donne pas accès à cette séquence, il faut le
-- faire séparément (constaté : "permission denied for sequence
-- variations_fournisseur_id_seq" sur un simple insert/upsert).
GRANT USAGE, SELECT ON SEQUENCE variations_fournisseur_id_seq
    TO anon, authenticated, service_role;

CREATE POLICY "Accès total variations_fournisseur"
    ON variations_fournisseur FOR ALL
    USING (true)
    WITH CHECK (true);


-- ============================================================
-- b) Modification de sku_variations_fournisseur
-- Remplace le texte libre couleur_fournisseur (et la colonne générée
-- reference_complete qui en dépendait) par une référence vers
-- variations_fournisseur.
-- Table actuellement vide (0 ligne) au moment d'écrire ce script : pas de
-- perte de données avec ce DROP COLUMN. À revérifier si ce n'est plus vrai
-- avant d'exécuter.
-- ============================================================

-- reference_complete doit être supprimée avant couleur_fournisseur (colonne
-- générée qui en dépend).
ALTER TABLE sku_variations_fournisseur DROP COLUMN IF EXISTS reference_complete;
ALTER TABLE sku_variations_fournisseur DROP COLUMN IF EXISTS couleur_fournisseur;

ALTER TABLE sku_variations_fournisseur
    ADD COLUMN IF NOT EXISTS id_variation BIGINT REFERENCES variations_fournisseur(id);

CREATE INDEX IF NOT EXISTS sku_variations_fournisseur_id_variation_idx
    ON sku_variations_fournisseur (id_variation);
