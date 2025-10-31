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


--
-- TOC entry 218 (class 1259 OID 16402)
-- Name: workflow; Type: TABLE; Schema: public; Owner: postgres
--


--
-- TOC entry 4849 (class 0 OID 16395)
-- Dependencies: 217
-- Data for Name: list_workflow; Type: TABLE DATA; Schema: public; Owner: postgres
--


--
-- TOC entry 4850 (class 0 OID 16402)
-- Dependencies: 218
-- Data for Name: workflow; Type: TABLE DATA; Schema: public; Owner: postgres
--


--
-- TOC entry 4700 (class 2606 OID 16401)
-- Name: list_workflow list_workflow_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

--
-- TOC entry 4702 (class 2606 OID 16414)
-- Name: list_workflow unique_task_name; Type: CONSTRAINT; Schema: public; Owner: postgres
--

--
-- TOC entry 4703 (class 2606 OID 16408)
-- Name: workflow fk_task_name; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--


-- Completed on 2025-04-17 05:04:44

--
-- PostgreSQL database dump complete
--
