-- ============================================================
-- Tables npc_parametrage / npc_groupes
-- Même structure que veiniere_parametrage / veiniere_groupes (cf.
-- sql/veiniere_parametrage.sql et sql/veiniere_groupes.sql), pour le
-- fournisseur NPC.
-- ============================================================

-- ── npc_parametrage ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS npc_parametrage (
    sku          TEXT        PRIMARY KEY,
    sku_parent   TEXT,
    nom_groupe   TEXT,
    id_variation BIGINT      REFERENCES variations_fournisseur(id),
    id_categorie BIGINT      REFERENCES categories_fournisseur(id),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS npc_parametrage_sku_parent_idx
    ON npc_parametrage (sku_parent);

CREATE INDEX IF NOT EXISTS npc_parametrage_id_variation_idx
    ON npc_parametrage (id_variation);

CREATE INDEX IF NOT EXISTS npc_parametrage_id_categorie_idx
    ON npc_parametrage (id_categorie);

ALTER TABLE npc_parametrage ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON npc_parametrage TO anon, authenticated, service_role;

CREATE POLICY "Accès total npc_parametrage"
    ON npc_parametrage FOR ALL
    USING (true)
    WITH CHECK (true);


-- ── npc_groupes ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS npc_groupes (
    sku_parent TEXT        PRIMARY KEY,
    nom_groupe TEXT        NOT NULL,
    categorie  TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE npc_groupes ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON npc_groupes TO anon, authenticated, service_role;

CREATE POLICY "Accès total npc_groupes"
    ON npc_groupes FOR ALL
    USING (true)
    WITH CHECK (true);
