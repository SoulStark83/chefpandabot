# -*- coding: utf-8 -*-
import os

BOT_TOKEN     = os.environ["BOT_TOKEN"]
ADMIN_CHAT    = os.environ["ADMIN_CHAT_ID"]
SUPABASE_URL  = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
PDF_BUCKET    = os.environ.get("SUPABASE_PDF_BUCKET", "")

VERSION_MODELO = "claude-haiku-4-5-20251001"
VERSION_PROMPT = "v2"

PLATAFORMAS_VALIDAS = {
    "google", "tripadvisor", "thefork", "facebook",
    "instagram", "justeat", "glovo", "ubereats", "yelp", "foursquare",
}

MODOS_VALIDOS = {"scraping", "manual", "api", "import", "disabled"}

CAMPOS_RESTAURANTE = (
    "id,nombre,ciudad,tipo_cocina,direccion,telefono,telegram_chat_id,"
    "plan,activo,fecha_alta,ultima_analisis,ultima_actualizacion_resenas,"
    "notas,metadata,slug,website,instagram_handle,barrio_zona,"
    "google_place_id,tripadvisor_url,thefork_url"
)
