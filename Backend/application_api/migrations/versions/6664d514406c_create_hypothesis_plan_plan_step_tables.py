"""create hypothesis, plan, plan_step tables

Revision ID: 6664d514406c
Revises: 4c83730acec9
Create Date: 2025-12-11 23:11:56.550761

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '6664d514406c'
down_revision: Union[str, Sequence[str], None] = '4c83730acec9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # ==========================================
    # PHASE 1: Drop ALL foreign key constraints
    # ==========================================
    
    # Chat-related FKs
    op.execute("ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_chat_id_fkey")
    op.execute("ALTER TABLE chat_study_association DROP CONSTRAINT IF EXISTS chat_study_association_chat_id_fkey")
    
    # Patient-related FKs
    op.execute("ALTER TABLE studies DROP CONSTRAINT IF EXISTS studies_patient_id_fkey")
    
    # Segmentation-related FKs (if radiomics has FK to segmentations)
    op.execute("ALTER TABLE radiomics_features DROP CONSTRAINT IF EXISTS radiomics_features_label_series_instance_uid_fkey")
    
    # ==========================================
    # PHASE 2: Convert PRIMARY KEY tables to UUID
    # ==========================================
    
    # 2.1 Convert chat_sessions
    print("Converting chat_sessions to UUID...")
    op.add_column('chat_sessions', sa.Column('chat_id_new', postgresql.UUID(as_uuid=True), 
                                             server_default=sa.text('gen_random_uuid()'), nullable=False))
    
    op.execute("""
        CREATE TEMP TABLE chat_id_mapping AS
        SELECT chat_id::text as old_id, chat_id_new as new_id
        FROM chat_sessions;
    """)
    
    op.execute("ALTER TABLE chat_sessions DROP CONSTRAINT chat_sessions_pkey")
    op.drop_column('chat_sessions', 'chat_id')
    op.alter_column('chat_sessions', 'chat_id_new', new_column_name='chat_id')
    op.execute("ALTER TABLE chat_sessions ADD PRIMARY KEY (chat_id)")
    
    # 2.2 Convert patients
    print("Converting patients to UUID...")
    op.add_column('patients', sa.Column('patient_id_new', postgresql.UUID(as_uuid=True), 
                                        server_default=sa.text('gen_random_uuid()'), nullable=False))
    
    op.execute("""
        CREATE TEMP TABLE patient_id_mapping AS
        SELECT patient_id::text as old_id, patient_id_new as new_id
        FROM patients;
    """)
    
    op.execute("ALTER TABLE patients DROP CONSTRAINT patients_pkey")
    op.drop_column('patients', 'patient_id')
    op.alter_column('patients', 'patient_id_new', new_column_name='patient_id')
    op.execute("ALTER TABLE patients ADD PRIMARY KEY (patient_id)")
    
    # 2.3 Convert models
    print("Converting models to UUID...")
    op.add_column('models', sa.Column('model_id_new', postgresql.UUID(as_uuid=True), 
                                      server_default=sa.text('gen_random_uuid()'), nullable=False))
    
    op.execute("ALTER TABLE models DROP CONSTRAINT models_pkey")
    op.drop_column('models', 'model_id')
    op.alter_column('models', 'model_id_new', new_column_name='model_id')
    op.execute("ALTER TABLE models ADD PRIMARY KEY (model_id)")
    
    # ==========================================
    # PHASE 3: Convert CHILD tables and restore FKs
    # ==========================================
    
    # 3.1 Convert chat_messages (has both PK and FK)
    print("Converting chat_messages to UUID...")
    op.add_column('chat_messages', sa.Column('message_id_new', postgresql.UUID(as_uuid=True), 
                                             server_default=sa.text('gen_random_uuid()'), nullable=False))
    op.add_column('chat_messages', sa.Column('chat_id_new', postgresql.UUID(as_uuid=True), nullable=True))
    
    # Map old chat_id to new UUID
    op.execute("""
        UPDATE chat_messages cm
        SET chat_id_new = m.new_id
        FROM chat_id_mapping m
        WHERE cm.chat_id::text = m.old_id;
    """)
    
    op.execute("ALTER TABLE chat_messages DROP CONSTRAINT chat_messages_pkey")
    op.drop_column('chat_messages', 'message_id')
    op.drop_column('chat_messages', 'chat_id')
    op.alter_column('chat_messages', 'message_id_new', new_column_name='message_id')
    op.alter_column('chat_messages', 'chat_id_new', new_column_name='chat_id', nullable=False)
    op.execute("ALTER TABLE chat_messages ADD PRIMARY KEY (message_id)")
    op.create_foreign_key('chat_messages_chat_id_fkey', 'chat_messages', 'chat_sessions', 
                         ['chat_id'], ['chat_id'], ondelete='CASCADE')
    
    # 3.2 Convert chat_study_association
    print("Converting chat_study_association to UUID...")
    op.add_column('chat_study_association', sa.Column('chat_id_new', postgresql.UUID(as_uuid=True), nullable=True))
    
    op.execute("""
        UPDATE chat_study_association csa
        SET chat_id_new = m.new_id
        FROM chat_id_mapping m
        WHERE csa.chat_id::text = m.old_id;
    """)
    
    op.drop_column('chat_study_association', 'chat_id')
    op.alter_column('chat_study_association', 'chat_id_new', new_column_name='chat_id', nullable=False)
    op.create_foreign_key('chat_study_association_chat_id_fkey', 'chat_study_association', 'chat_sessions', 
                         ['chat_id'], ['chat_id'], ondelete='CASCADE')
    
    # 3.3 Convert studies.patient_id (FK to patients)
    print("Converting studies.patient_id to UUID...")
    op.add_column('studies', sa.Column('patient_id_new', postgresql.UUID(as_uuid=True), nullable=True))
    
    op.execute("""
        UPDATE studies s
        SET patient_id_new = m.new_id
        FROM patient_id_mapping m
        WHERE s.patient_id::text = m.old_id;
    """)
    
    op.drop_column('studies', 'patient_id')
    op.alter_column('studies', 'patient_id_new', new_column_name='patient_id', nullable=False)
    op.create_foreign_key('studies_patient_id_fkey', 'studies', 'patients', 
                         ['patient_id'], ['patient_id'], ondelete='CASCADE')
    
    # 3.4 Convert radiomics_features.id (just PK, no FK conversion needed for label_series_instance_uid)
    print("Converting radiomics_features to UUID...")
    op.add_column('radiomics_features', sa.Column('id_new', postgresql.UUID(as_uuid=True), 
                                                  server_default=sa.text('gen_random_uuid()'), nullable=False))
    
    op.execute("ALTER TABLE radiomics_features DROP CONSTRAINT radiomics_features_pkey")
    op.drop_column('radiomics_features', 'id')
    op.alter_column('radiomics_features', 'id_new', new_column_name='id')
    op.execute("ALTER TABLE radiomics_features ADD PRIMARY KEY (id)")
    
    # Restore FK to segmentations if it exists
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'segmentations') THEN
                ALTER TABLE radiomics_features 
                ADD CONSTRAINT radiomics_features_label_series_instance_uid_fkey 
                FOREIGN KEY (label_series_instance_uid) 
                REFERENCES segmentations(label_series_instance_uid) 
                ON DELETE CASCADE;
            END IF;
        END $$;
    """)
    
    # ==========================================
    # PHASE 4: Create new tables
    # ==========================================
    
    print("Creating hypotheses, plans, and plan_steps tables...")
    
    op.create_table(
        'hypotheses',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, 
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('chat_id', postgresql.UUID(as_uuid=True), 
                 sa.ForeignKey('chat_sessions.chat_id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('population', sa.Text()),
        sa.Column('intervention', sa.Text()),
        sa.Column('outcome', sa.Text()),
        sa.Column('clinical_context', sa.Text()),
        sa.Column('relationship_type', sa.String(50)),
        sa.Column('state', sa.String(50)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.UniqueConstraint('chat_id')
    )
    
    op.create_table(
        'plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, 
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('hypothesis_id', postgresql.UUID(as_uuid=True), 
                 sa.ForeignKey('hypotheses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('study_goal', sa.String(255), nullable=False),
        sa.Column('state', sa.String(50)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'))
    )
    
    op.create_table(
        'plan_steps',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, 
                 server_default=sa.text('gen_random_uuid()')),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), 
                 sa.ForeignKey('plans.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('module', sa.String(255)),
        sa.Column('status', sa.String(50), server_default='not_confirmed'),
        sa.Column('order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'))
    )
    
    print("Migration completed successfully!")

def downgrade():
    # This downgrade collapses every UUID primary key back to the literal
    # integer 1 ("USING 1" below), destroying row identity for every table
    # it touches and making the subsequent foreign-key recreation fail on
    # any database with more than one row per table. There is no safe
    # automated reversal of the UUID conversion; restore from a pre-upgrade
    # backup instead of downgrading past this revision.
    raise NotImplementedError(
        "Downgrading past 6664d514406c is destructive (it collapses every "
        "converted UUID primary key to a single shared value) and is "
        "intentionally disabled. Restore from a backup taken before this "
        "revision was applied instead."
    )
    # Drop new tables first
    op.drop_table('plan_steps')
    op.drop_table('plans')
    op.drop_table('hypotheses')
    
    # Drop foreign keys
    op.drop_constraint('chat_messages_chat_id_fkey', 'chat_messages', type_='foreignkey')
    op.drop_constraint('chat_study_association_chat_id_fkey', 'chat_study_association', type_='foreignkey')
    op.drop_constraint('studies_patient_id_fkey', 'studies', type_='foreignkey')
    op.execute("ALTER TABLE radiomics_features DROP CONSTRAINT IF EXISTS radiomics_features_label_series_instance_uid_fkey")
    
    # Revert UUIDs back to INTEGER (simplified - you'll lose data)
    op.execute("ALTER TABLE chat_sessions ALTER COLUMN chat_id TYPE INTEGER USING 1")
    op.execute("ALTER TABLE patients ALTER COLUMN patient_id TYPE INTEGER USING 1")
    op.execute("ALTER TABLE models ALTER COLUMN model_id TYPE INTEGER USING 1")
    op.execute("ALTER TABLE chat_messages ALTER COLUMN message_id TYPE INTEGER USING 1")
    op.execute("ALTER TABLE chat_messages ALTER COLUMN chat_id TYPE INTEGER USING 1")
    op.execute("ALTER TABLE radiomics_features ALTER COLUMN id TYPE INTEGER USING 1")
    op.execute("ALTER TABLE studies ALTER COLUMN patient_id TYPE INTEGER USING 1")
    op.execute("ALTER TABLE chat_study_association ALTER COLUMN chat_id TYPE INTEGER USING 1")
    
    # Recreate foreign keys (will fail if data doesn't match)
    op.create_foreign_key('chat_messages_chat_id_fkey', 'chat_messages', 'chat_sessions', 
                         ['chat_id'], ['chat_id'], ondelete='CASCADE')
    op.create_foreign_key('chat_study_association_chat_id_fkey', 'chat_study_association', 'chat_sessions', 
                         ['chat_id'], ['chat_id'], ondelete='CASCADE')
    op.create_foreign_key('studies_patient_id_fkey', 'studies', 'patients', 
                         ['patient_id'], ['patient_id'], ondelete='CASCADE')

