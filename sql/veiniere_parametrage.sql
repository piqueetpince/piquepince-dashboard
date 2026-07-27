-- ============================================================
-- a) Table categories_fournisseur
-- Catégories de produits par fournisseur (ex: Barrette / Pince / Pic &
-- épingle pour VEINIERE), référencées par veiniere_parametrage.id_categorie.
-- ============================================================

CREATE TABLE IF NOT EXISTS categories_fournisseur (
    id          BIGSERIAL   PRIMARY KEY,
    fournisseur TEXT        NOT NULL,
    categorie   TEXT        NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (fournisseur, categorie)
);

ALTER TABLE categories_fournisseur ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON categories_fournisseur TO anon, authenticated, service_role;

-- BIGSERIAL crée une séquence implicite ; le GRANT sur la table seule ne
-- donne pas accès à cette séquence (déjà rencontré sur variations_fournisseur).
GRANT USAGE, SELECT ON SEQUENCE categories_fournisseur_id_seq
    TO anon, authenticated, service_role;

CREATE POLICY "Accès total categories_fournisseur"
    ON categories_fournisseur FOR ALL
    USING (true)
    WITH CHECK (true);


-- ============================================================
-- b) Table veiniere_parametrage
-- Paramétrage par SKU pour le fournisseur Veinière : rattachement au SKU
-- parent, groupe de nom, variation fournisseur et catégorie.
-- ============================================================

CREATE TABLE IF NOT EXISTS veiniere_parametrage (
    sku          TEXT        PRIMARY KEY,
    sku_parent   TEXT,
    nom_groupe   TEXT,
    id_variation BIGINT      REFERENCES variations_fournisseur(id),
    id_categorie BIGINT      REFERENCES categories_fournisseur(id),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS veiniere_parametrage_sku_parent_idx
    ON veiniere_parametrage (sku_parent);

CREATE INDEX IF NOT EXISTS veiniere_parametrage_id_variation_idx
    ON veiniere_parametrage (id_variation);

CREATE INDEX IF NOT EXISTS veiniere_parametrage_id_categorie_idx
    ON veiniere_parametrage (id_categorie);

ALTER TABLE veiniere_parametrage ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON veiniere_parametrage TO anon, authenticated, service_role;

CREATE POLICY "Accès total veiniere_parametrage"
    ON veiniere_parametrage FOR ALL
    USING (true)
    WITH CHECK (true);


-- ============================================================
-- 2. Initialisation des catégories Veinière
-- ============================================================

INSERT INTO categories_fournisseur (fournisseur, categorie) VALUES
    ('VEINIERE', 'Barrette'),
    ('VEINIERE', 'Pince'),
    ('VEINIERE', 'Pic & épingle')
ON CONFLICT (fournisseur, categorie) DO NOTHING;
