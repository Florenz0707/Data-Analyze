from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class DeepseekApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'deepseek_api'

    def ready(self):
        # Eager-load model and vector index when the web service starts.
        # This will be a no-op for non-runserver commands due to guards in services.preload_system.
        try:
            from django.conf import settings
            import os
            if getattr(settings, 'PRELOAD_LLM_ON_STARTUP', False) and getattr(settings, 'ENABLE_LLM', True):
                # 避免 Django autoreloader 导致的双次加载：仅在子进程中预加载
                is_reloader_child = os.environ.get('RUN_MAIN') == 'true' or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
                if is_reloader_child or not getattr(settings, 'DEBUG', False):
                    from .services import preload_system
                    preload_system()
                    logger.info("DeepseekApiConfig.ready: preload_system invoked")
                else:
                    logger.info("DeepseekApiConfig.ready: skipped preload in autoreloader parent process")
            else:
                logger.info("DeepseekApiConfig.ready: preload skipped by settings")
        except Exception as e:
            # Avoid breaking startup for management commands or unexpected environments
            logger.info(f"DeepseekApiConfig.ready: preload skipped ({e})")
