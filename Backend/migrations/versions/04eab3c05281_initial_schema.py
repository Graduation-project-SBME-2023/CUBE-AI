"""initial schema

Revision ID: 04eab3c05281
Revises: 
Create Date: 2025-10-23 21:46:49.237015

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04eab3c05281'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
     op.execute("""
                

create table patients
(
    patient_id serial
        primary key,
    name       varchar(255) not null,
    birth_date date         not null
        constraint patients_birth_date_check
            check (birth_date <= CURRENT_DATE),
    gender     varchar(10)
        constraint patients_gender_check
            check ((gender)::text = ANY ((ARRAY ['M'::character varying, 'F'::character varying])::text[])),
    created_at timestamp default CURRENT_TIMESTAMP,
    unique (name, birth_date)
);

alter table patients
    owner to workflow_user;

create index idx_patients_patient_id
    on patients (patient_id);

create table studies
(
    study_instance_uid text    not null
        primary key,
    patient_id         integer not null
        references patients
            on delete cascade,
    description        text,
    modality           varchar(20),
    created_at         timestamp default CURRENT_TIMESTAMP
);

alter table studies
    owner to workflow_user;

create index idx_studies_patient_id
    on studies (patient_id);

create table volume_series
(
    series_instance_uid text                                not null
        primary key,
    study_instance_uid  text                                not null
        references studies
            on delete cascade,
    series_description  text,
    modality            varchar(20),
    num_slices          integer                             not null
        constraint volume_series_num_slices_check
            check (num_slices > 0),
    created_at          timestamp default CURRENT_TIMESTAMP not null
);

alter table volume_series
    owner to workflow_user;

create index idx_series_study_id
    on volume_series (study_instance_uid);

create table segmentations
(
    segmentation_id     serial
        primary key,
    series_instance_uid text not null
        references volume_series
            on delete cascade,
    segmentation_type   text not null,
    method              varchar(255),
    model               varchar(255),
    created_at          timestamp default CURRENT_TIMESTAMP
);

alter table segmentations
    owner to workflow_user;

create index idx_segmentations_series_method
    on segmentations (series_instance_uid, method);

create table radiomics_features
(
    id              serial
        primary key,
    segmentation_id integer not null
        references segmentations
            on delete cascade,
    features        jsonb   not null
        constraint radiomics_features_features_check
            check (jsonb_typeof(features) = 'object'::text),
    created_at      timestamp default CURRENT_TIMESTAMP
);

alter table radiomics_features
    owner to workflow_user;

create index idx_radiomics_segmentation_id
    on radiomics_features (segmentation_id);

create table chat_sessions
(
    chat_id            serial
        primary key,
    study_instance_uid text not null
        references studies
            on delete cascade,
    title              varchar(255),
    started_at         timestamp default CURRENT_TIMESTAMP,
    last_activity      timestamp,
    is_active          boolean   default true,
    summary            text,
    constraint chat_sessions_check
        check ((last_activity IS NULL) OR (last_activity >= started_at))
);

alter table chat_sessions
    owner to workflow_user;

create index idx_chat_study_active
    on chat_sessions (study_instance_uid, is_active);

create table models
(
    model_id      serial
        primary key,
    name          varchar(100) not null
        unique,
    description   text,
    configuration jsonb        not null
        constraint models_configuration_check
            check (jsonb_typeof(configuration) = 'object'::text),
    created_at    timestamp default CURRENT_TIMESTAMP
);

alter table models
    owner to workflow_user;

        """)    
    


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS models CASCADE;
        DROP TABLE IF EXISTS chat_sessions CASCADE;
        DROP TABLE IF EXISTS radiomics_features CASCADE;
        DROP TABLE IF EXISTS segmentations CASCADE;
        DROP TABLE IF EXISTS volume_series CASCADE;
        DROP TABLE IF EXISTS studies CASCADE;
        DROP TABLE IF EXISTS patients CASCADE;
        DROP TABLE IF EXISTS list_workflows CASCADE;
        DROP TABLE IF EXISTS workflows CASCADE;
    """)