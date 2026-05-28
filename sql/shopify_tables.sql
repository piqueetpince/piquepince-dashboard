-- ============================================================
-- Tables Shopify — multi-boutique
-- Valeurs acceptées pour la colonne boutique :
--   'foulard_frenchy' | 'montessori' | 'maison_foulard'
-- Clé primaire composée (id_shopify, boutique) car le même
-- GID Shopify peut exister dans deux boutiques distinctes.
-- ============================================================


-- ── 1. produits_shopify ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS produits_shopify (
    id_shopify      TEXT        NOT NULL,
    boutique        TEXT        NOT NULL CHECK (boutique IN ('foulard_frenchy', 'montessori', 'maison_foulard')),
    legacy_id       BIGINT,
    handle          TEXT,
    titre           TEXT,
    statut          TEXT        CHECK (statut IN ('ACTIVE', 'ARCHIVED', 'DRAFT')),
    type_produit    TEXT,
    fournisseur     TEXT,
    tags            TEXT[],
    total_stock     INT,
    publie_le       TIMESTAMPTZ,
    cree_le         TIMESTAMPTZ,
    mis_a_jour_le   TIMESTAMPTZ,

    PRIMARY KEY (id_shopify, boutique)
);

CREATE UNIQUE INDEX IF NOT EXISTS produits_shopify_legacy_boutique_idx
    ON produits_shopify (legacy_id, boutique);

CREATE INDEX IF NOT EXISTS produits_shopify_boutique_idx
    ON produits_shopify (boutique);

CREATE INDEX IF NOT EXISTS produits_shopify_statut_idx
    ON produits_shopify (boutique, statut);

ALTER TABLE produits_shopify ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON produits_shopify TO anon, authenticated;

CREATE POLICY "Accès total produits_shopify"
    ON produits_shopify FOR ALL
    USING (true)
    WITH CHECK (true);


-- ── 2. produits_shopify_variants ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS produits_shopify_variants (
    id_shopify          TEXT        NOT NULL,
    boutique            TEXT        NOT NULL CHECK (boutique IN ('foulard_frenchy', 'montessori', 'maison_foulard')),
    legacy_id           BIGINT,
    id_produit_shopify  TEXT        NOT NULL,
    sku                 TEXT,
    titre               TEXT,
    nom_complet         TEXT,
    prix                NUMERIC(10, 2),
    prix_compare        NUMERIC(10, 2),
    stock               INT,
    disponible          BOOLEAN,
    code_barre          TEXT,
    poids_g             NUMERIC(8, 2),
    taxable             BOOLEAN,
    position            INT,
    cree_le             TIMESTAMPTZ,
    mis_a_jour_le       TIMESTAMPTZ,

    PRIMARY KEY (id_shopify, boutique),
    FOREIGN KEY (id_produit_shopify, boutique)
        REFERENCES produits_shopify (id_shopify, boutique)
        ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS produits_shopify_variants_legacy_boutique_idx
    ON produits_shopify_variants (legacy_id, boutique);

CREATE INDEX IF NOT EXISTS produits_shopify_variants_produit_idx
    ON produits_shopify_variants (id_produit_shopify, boutique);

CREATE INDEX IF NOT EXISTS produits_shopify_variants_sku_idx
    ON produits_shopify_variants (boutique, sku);

ALTER TABLE produits_shopify_variants ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON produits_shopify_variants TO anon, authenticated;

CREATE POLICY "Accès total produits_shopify_variants"
    ON produits_shopify_variants FOR ALL
    USING (true)
    WITH CHECK (true);


-- ── 3. commandes_shopify ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commandes_shopify (
    id_shopify              TEXT        NOT NULL,
    boutique                TEXT        NOT NULL CHECK (boutique IN ('foulard_frenchy', 'montessori', 'maison_foulard')),
    legacy_id               BIGINT,
    numero                  TEXT,
    numero_seq              INT,
    cree_le                 TIMESTAMPTZ,
    traite_le               TIMESTAMPTZ,
    mis_a_jour_le           TIMESTAMPTZ,
    annule_le               TIMESTAMPTZ,
    statut_financier        TEXT,
    statut_livraison        TEXT,
    montant_ttc             NUMERIC(10, 2),
    montant_ht_approx       NUMERIC(10, 2),
    montant_taxes           NUMERIC(10, 2),
    frais_port              NUMERIC(10, 2),
    montant_remises         NUMERIC(10, 2),
    devise                  TEXT,
    taxes_incluses          BOOLEAN,
    -- Client
    id_client_shopify       TEXT,
    email_client            TEXT,
    prenom_client           TEXT,
    nom_client              TEXT,
    -- Adresse facturation
    nom_facturation         TEXT,
    adresse_facturation     TEXT,
    ville_facturation       TEXT,
    cp_facturation          TEXT,
    pays_facturation_iso    TEXT,
    -- Adresse livraison
    nom_livraison           TEXT,
    adresse_livraison       TEXT,
    ville_livraison         TEXT,
    cp_livraison            TEXT,
    pays_livraison_iso      TEXT,
    -- Divers
    tags                    TEXT[],
    note                    TEXT,
    source                  TEXT,
    commande_test           BOOLEAN     DEFAULT FALSE,

    PRIMARY KEY (id_shopify, boutique)
);

CREATE UNIQUE INDEX IF NOT EXISTS commandes_shopify_legacy_boutique_idx
    ON commandes_shopify (legacy_id, boutique);

CREATE INDEX IF NOT EXISTS commandes_shopify_boutique_idx
    ON commandes_shopify (boutique);

CREATE INDEX IF NOT EXISTS commandes_shopify_cree_le_idx
    ON commandes_shopify (boutique, cree_le DESC);

CREATE INDEX IF NOT EXISTS commandes_shopify_statut_financier_idx
    ON commandes_shopify (boutique, statut_financier);

CREATE INDEX IF NOT EXISTS commandes_shopify_client_idx
    ON commandes_shopify (boutique, id_client_shopify);

ALTER TABLE commandes_shopify ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON commandes_shopify TO anon, authenticated;

CREATE POLICY "Accès total commandes_shopify"
    ON commandes_shopify FOR ALL
    USING (true)
    WITH CHECK (true);


-- ── 4. lignes_commande_shopify ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lignes_commande_shopify (
    id_shopify              TEXT        NOT NULL,
    boutique                TEXT        NOT NULL CHECK (boutique IN ('foulard_frenchy', 'montessori', 'maison_foulard')),
    id_commande_shopify     TEXT        NOT NULL,
    id_variant_shopify      TEXT,
    sku                     TEXT,
    titre                   TEXT,
    quantite                INT,
    prix_unitaire_original  NUMERIC(10, 2),
    prix_unitaire_remise    NUMERIC(10, 2),
    total_remise            NUMERIC(10, 2),
    taxable                 BOOLEAN,

    PRIMARY KEY (id_shopify, boutique),
    FOREIGN KEY (id_commande_shopify, boutique)
        REFERENCES commandes_shopify (id_shopify, boutique)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS lignes_commande_shopify_commande_idx
    ON lignes_commande_shopify (id_commande_shopify, boutique);

CREATE INDEX IF NOT EXISTS lignes_commande_shopify_sku_idx
    ON lignes_commande_shopify (boutique, sku);

ALTER TABLE lignes_commande_shopify ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON lignes_commande_shopify TO anon, authenticated;

CREATE POLICY "Accès total lignes_commande_shopify"
    ON lignes_commande_shopify FOR ALL
    USING (true)
    WITH CHECK (true);
