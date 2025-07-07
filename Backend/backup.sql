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
-- Name: list_workflow; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.list_workflow (
    task_name text NOT NULL
);


ALTER TABLE public.list_workflow OWNER TO workflow_user;

--
-- TOC entry 218 (class 1259 OID 16402)
-- Name: workflow; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.workflow (
    case_id text,
    task_name text,
    series_instance_uid text,
    instance_uid text,
    annotated boolean DEFAULT false,
    train boolean,
    modality text
);


ALTER TABLE public.workflow OWNER TO workflow_user;

--
-- TOC entry 4849 (class 0 OID 16395)
-- Dependencies: 217
-- Data for Name: list_workflow; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.list_workflow (task_name) FROM stdin;
\.


--
-- TOC entry 4850 (class 0 OID 16402)
-- Dependencies: 218
-- Data for Name: workflow; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.workflow (case_id, task_name, series_instance_uid, instance_uid, annotated, train, modality) FROM stdin;
\.


--
-- TOC entry 4700 (class 2606 OID 16401)
-- Name: list_workflow list_workflow_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.list_workflow
    ADD CONSTRAINT list_workflow_pkey PRIMARY KEY (task_name);


--
-- TOC entry 4702 (class 2606 OID 16414)
-- Name: list_workflow unique_task_name; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.list_workflow
    ADD CONSTRAINT unique_task_name UNIQUE (task_name);


--
-- TOC entry 4703 (class 2606 OID 16408)
-- Name: workflow fk_task_name; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.workflow
    ADD CONSTRAINT fk_task_name FOREIGN KEY (task_name) REFERENCES public.list_workflow(task_name) ON DELETE CASCADE;


-- Completed on 2025-04-17 05:04:44

--
-- PostgreSQL database dump complete
--

