#!/usr/bin/env python3
"""
SQLAlchemy metadata cache temizleme ve model yenileme scripti
"""

import logging
from app.executor.ledger import engine, Base

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def refresh_sqlalchemy_metadata():
    """
    SQLAlchemy metadata'ını temizle ve yenile
    """
    try:
        # Metadata'ı yenile
        Base.metadata.clear()

        # Tablo varlıklarını yeniden oluştur
        Base.metadata.reflect(bind=engine, only=['prediction_logs'])

        logger.info("✅ SQLAlchemy metadata başarıyla yenilendi")

        # Model kontrolü
        from app.executor.ledger import PredictionLog

        # Model sütunlarını kontrol et
        table_columns = Base.metadata.tables['prediction_logs'].columns.keys()
        model_columns = [column.name for column in PredictionLog.__table__.columns]

        logger.info(f"Veritabanı sütunları: {len(table_columns)}")
        logger.info(f"Model sütunları: {len(model_columns)}")

        # Uyuşmazlığı kontrol et
        missing_in_model = set(table_columns) - set(model_columns)
        missing_in_db = set(model_columns) - set(table_columns)

        if missing_in_model:
            logger.warning(f"Modelde olmayan veritabanı sütunları: {missing_in_model}")
        if missing_in_db:
            logger.warning(f"Veritabanında olmayan model sütunları: {missing_in_db}")

        if not missing_in_model and not missing_in_db:
            logger.info("✅ Model ve veritabanı şeması uyumlu")

        return True

    except Exception as e:
        logger.error(f"❌ Metadata yenileme hatası: {e}")
        return False

if __name__ == "__main__":
    success = refresh_sqlalchemy_metadata()
    if success:
        print("🎉 SQLAlchemy metadata başarıyla yenilendi")
    else:
        print("❌ Metadata yenileme başarısız")