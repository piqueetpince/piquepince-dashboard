-- ============================================================
-- Table veiniere_groupes
-- Nom de groupe canonique par SKU parent (ex: "Barrette à cheveux Carla"
-- pour le préfixe BAR0009), pour la page "⚙️ Paramétrage Veinière".
-- ============================================================

CREATE TABLE IF NOT EXISTS veiniere_groupes (
    sku_parent TEXT        PRIMARY KEY,
    nom_groupe TEXT        NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE veiniere_groupes ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON veiniere_groupes TO anon, authenticated, service_role;

CREATE POLICY "Accès total veiniere_groupes"
    ON veiniere_groupes FOR ALL
    USING (true)
    WITH CHECK (true);
