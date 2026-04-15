from supabase import Client, create_client

from app.config import get_settings

settings = get_settings()

supabase: Client = create_client(settings.supabase_url, settings.supabase_service_role_key)


def get_supabase() -> Client:
    return supabase
