--
-- PostgreSQL database dump
--

-- Dumped from database version 17.4
-- Dumped by pg_dump version 17.4

-- Started on 2025-04-17 05:04:43

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- TOC entry 217 (class 1259 OID 16395)
-- Name: workflow; Type: TABLE; Schema: public; Owner: workflow_user
--

CREATE TABLE public.workflow (
    task_name text NOT NULL PRIMARY KEY UNIQUE
);


ALTER TABLE public.workflow OWNER TO workflow_user;

--
-- TOC entry 218 (class 1259 OID 16402)
-- Name: workflow_details; Type: TABLE; Schema: public; Owner: workflow_user
--

CREATE TABLE public.workflow_details (
    task_name text NOT NULL,
    study_UID text NOT NULL,
    volume_series_instance_UID text NOT NULL,
    label_series_instance_UID text,
    annotated boolean DEFAULT false,
    train boolean DEFAULT true,
    modality text,
    CONSTRAINT workflow_details_pkey PRIMARY KEY (task_name, study_UID, volume_series_instance_UID),
    CONSTRAINT fk_task_name FOREIGN KEY (task_name) REFERENCES public.workflow(task_name) ON DELETE CASCADE
);


ALTER TABLE public.workflow_details OWNER TO workflow_user;


-- Completed on 2025-04-17 05:04:44

--
-- PostgreSQL database dump complete
--
