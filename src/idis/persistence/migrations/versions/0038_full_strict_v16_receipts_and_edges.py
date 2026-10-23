"""Install the frozen full-strict-v21 receipt and consumer-edge spine.

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-22

The reviewed preimage is the sole normative byte source.  This revision pins
its raw file digest and the two admitted contract digests before translating
the normalized DDL tuples into PostgreSQL.  The 38 future relations remain
frozen and absent; only CURRENT48 erasure authority is installed.
"""

# ruff: noqa: E501 -- reviewed SQL/DDL literals are preserved verbatim for auditability.

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final, NamedTuple
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

FULL_STRICT_V21_CONTRACT_HASH: Final = (
    "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
)
FULL_STRICT_V21_ACTIVATION_STATE: Final = "INACTIVE"
TASK7_DDL_REGISTRY_HASH: Final = "073d2b19f16c6e41529ac6aba970d8e09040094103d7951fd5520d88646aedcd"
TASK7_SQL_AUTHORITY_REGISTRY_HASH: Final = (
    "cf65a1e633bd242c44a2da74861bf4a17aa19fe865625e37ebdb33910889e526"
)
_REVIEWED_ARTIFACT_SHA256: Final = (
    "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
)
_REVIEWED_FIXTURE_SHA256: Final = "da79338f41e0555d1304c162e249246086db26b20421626dd9c0d51690fd7773"
TASK7_CURRENT_GUARD_ROUTINE_COUNT: Final = 24
TASK7_CURRENT_GUARD_TARGET_COUNT: Final = 73
TASK7_CURRENT_GUARD_EVENT_COUNT: Final = 193
TASK7_GUARD_PARENT_RELATION_COUNT: Final = 36
TASK7_GUARD_PARENT_COLUMN_GRANT_COUNT: Final = 424
_TASK7_CURRENT_GUARD_BODY_PREDECESSOR_SQL_V1: Final = (
    "CREATE FUNCTION public.idis_task7_source_canonical_jsonb_text_v1(p_value jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$ DECLARE kind text; rendered text; numeric_text text; finite_value double precision; BEGIN kind:=pg_catalog.jsonb_typeof(p_value); IF kind='null' THEN RETURN 'null'; ELSIF kind='boolean' THEN RETURN p_value::text; ELSIF kind='number' THEN numeric_text:=p_value::text; IF numeric_text~'^-?(0|[1-9][0-9]*)$' THEN RETURN numeric_text; END IF; finite_value:=numeric_text::double precision; IF finite_value::text IN ('NaN','Infinity','-Infinity') THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; IF finite_value=0::double precision THEN finite_value:=0::double precision; END IF; RETURN '{\"float8_be\":' ||pg_catalog.to_json( pg_catalog.encode(pg_catalog.float8send(finite_value),'hex') )::text ||'}'; ELSIF kind='string' THEN RETURN pg_catalog.to_json(normalize(p_value#>>'{}',NFC))::text; ELSIF kind='array' THEN SELECT '[' ||COALESCE( pg_catalog.string_agg( public.idis_task7_source_canonical_jsonb_text_v1(item), ',' ORDER BY ordinal ), '' ) ||']' INTO rendered FROM pg_catalog.jsonb_array_elements(p_value) WITH ORDINALITY AS values(item,ordinal); RETURN rendered; ELSIF kind='object' THEN IF EXISTS ( SELECT 1 FROM pg_catalog.jsonb_object_keys(p_value) AS keys(key) GROUP BY normalize(key,NFC) HAVING count(*)<>1 ) THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; SELECT '{' ||COALESCE( pg_catalog.string_agg( pg_catalog.to_json(normalize(key,NFC))::text ||':' ||public.idis_task7_source_canonical_jsonb_text_v1(value), ',' ORDER BY normalize(key,NFC) COLLATE \"C\" ), '' ) ||'}' INTO rendered FROM pg_catalog.jsonb_each(p_value); RETURN rendered; END IF; RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END $$",
    "CREATE FUNCTION public.idis_task7_source_hash_jsonb_v1(p_value jsonb) RETURNS text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$ SELECT pg_catalog.encode( pg_catalog.sha256( pg_catalog.convert_to( public.idis_task7_source_canonical_jsonb_text_v1(p_value), 'UTF8' ) ), 'hex' ) $$",
    "CREATE FUNCTION public.idis_task7_sha1_v1(p_value bytea) RETURNS bytea LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$ DECLARE payload bytea:=p_value; original_octets bigint:=pg_catalog.octet_length(p_value); zero_count integer; block_offset integer; word_ordinal integer; round_ordinal integer; words bigint[]:=pg_catalog.array_fill(0::bigint,ARRAY[80],ARRAY[0]); h0 bigint:=1732584193; h1 bigint:=4023233417; h2 bigint:=2562383102; h3 bigint:=271733878; h4 bigint:=3285377520; a bigint; b bigint; c bigint; d bigint; e bigint; f bigint; k bigint; temporary bigint; mask constant bigint:=4294967295; length_hex text; digest_hex text; BEGIN IF p_value IS NULL THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; payload:=payload||pg_catalog.decode('80','hex'); zero_count:=(56-(pg_catalog.octet_length(payload)%64)+64)%64; IF zero_count>0 THEN payload:=payload||pg_catalog.decode(repeat('00',zero_count),'hex'); END IF; length_hex:=pg_catalog.lpad(pg_catalog.to_hex(original_octets*8),16,'0'); payload:=payload||pg_catalog.decode(length_hex,'hex'); block_offset:=0; WHILE block_offset<pg_catalog.octet_length(payload) LOOP FOR word_ordinal IN 0..15 LOOP words[word_ordinal]:=( (pg_catalog.get_byte(payload,block_offset+word_ordinal*4)::bigint<<24) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+1)::bigint<<16) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+2)::bigint<<8) | pg_catalog.get_byte(payload,block_offset+word_ordinal*4+3)::bigint )&mask; END LOOP; FOR word_ordinal IN 16..79 LOOP temporary:=( words[word_ordinal-3] # words[word_ordinal-8] # words[word_ordinal-14] # words[word_ordinal-16] )&mask; words[word_ordinal]:=((temporary<<1)|(temporary>>31))&mask; END LOOP; a:=h0; b:=h1; c:=h2; d:=h3; e:=h4; FOR round_ordinal IN 0..79 LOOP IF round_ordinal<=19 THEN f:=((b&c)|((mask#b)&d))&mask; k:=1518500249; ELSIF round_ordinal<=39 THEN f:=(b#c#d)&mask; k:=1859775393; ELSIF round_ordinal<=59 THEN f:=((b&c)|(b&d)|(c&d))&mask; k:=2400959708; ELSE f:=(b#c#d)&mask; k:=3395469782; END IF; temporary:=( (((a<<5)|(a>>27))&mask)+f+e+k+words[round_ordinal] )&mask; e:=d; d:=c; c:=((b<<30)|(b>>2))&mask; b:=a; a:=temporary; END LOOP; h0:=(h0+a)&mask; h1:=(h1+b)&mask; h2:=(h2+c)&mask; h3:=(h3+d)&mask; h4:=(h4+e)&mask; block_offset:=block_offset+64; END LOOP; digest_hex:= pg_catalog.lpad(pg_catalog.to_hex(h0),8,'0') ||pg_catalog.lpad(pg_catalog.to_hex(h1),8,'0') ||pg_catalog.lpad(pg_catalog.to_hex(h2),8,'0') ||pg_catalog.lpad(pg_catalog.to_hex(h3),8,'0') ||pg_catalog.lpad(pg_catalog.to_hex(h4),8,'0'); RETURN pg_catalog.decode(digest_hex,'hex'); END $$",
    "CREATE FUNCTION public.idis_task7_uuidv5_v1(p_namespace uuid,p_name text) RETURNS uuid LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$ DECLARE namespace_bytes bytea; digest bytea; value_hex text; BEGIN IF p_namespace IS NULL OR p_name IS NULL THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; namespace_bytes:=pg_catalog.decode( replace(p_namespace::text,'-',''), 'hex' ); digest:=pg_catalog.substring( public.idis_task7_sha1_v1( namespace_bytes||pg_catalog.convert_to(p_name,'UTF8') ), 1, 16 ); digest:=pg_catalog.set_byte( digest, 6, (pg_catalog.get_byte(digest,6)&15)|80 ); digest:=pg_catalog.set_byte( digest, 8, (pg_catalog.get_byte(digest,8)&63)|128 ); value_hex:=pg_catalog.encode(digest,'hex'); RETURN ( pg_catalog.substring(value_hex,1,8) ||'-'||pg_catalog.substring(value_hex,9,4) ||'-'||pg_catalog.substring(value_hex,13,4) ||'-'||pg_catalog.substring(value_hex,17,4) ||'-'||pg_catalog.substring(value_hex,21,12) )::uuid; END $$",
    "CREATE FUNCTION public.idis_task7_source_projection_v1(p_root_id uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ DECLARE root_row record; package_row record; file_row record; artifact_row record; document_row record; attempt_row record; span_row record; manifest jsonb; manifest_file jsonb; manifest_members jsonb; metadata jsonb; schema_refs jsonb; terminal_projection jsonb; integrity jsonb; native_scope jsonb; expected_version text; expected_disposition text; expected_identity text; expected_content text; expected_corpus text; terminal_receipt text; locator_sha text; artifact_metadata_sha text; expected_root_id uuid; name_hash text; support_status text; triage_status text; storage_uri_sha text; matching_count bigint; BEGIN SELECT root.source_lineage_object_id, root.tenant_id, root.deal_id, root.corpus_receipt_id, root.object_kind, root.source_version, root.source_disposition, root.object_identity_sha256, root.content_sha256, root.corpus_receipt_sha256, root.write_origin INTO STRICT root_row FROM public.source_lineage_objects AS root WHERE root.source_lineage_object_id=p_root_id; IF root_row.write_origin<>'RUNTIME' THEN RETURN pg_catalog.jsonb_build_object( 'source_lineage_object_id',root_row.source_lineage_object_id::text, 'tenant_id',root_row.tenant_id::text, 'deal_id',root_row.deal_id::text, 'corpus_receipt_id',root_row.corpus_receipt_id::text, 'object_kind',root_row.object_kind, 'source_version',root_row.source_version, 'source_disposition',root_row.source_disposition, 'object_identity_sha256',root_row.object_identity_sha256, 'content_sha256',root_row.content_sha256, 'corpus_receipt_sha256',root_row.corpus_receipt_sha256, 'write_origin',root_row.write_origin ); END IF; IF root_row.object_kind='PACKAGE' THEN SELECT p.package_id,p.tenant_id,p.deal_id,p.status,p.metadata, p.source_binding_state,p.source_lineage_object_id INTO STRICT package_row FROM public.data_room_packages AS p WHERE p.source_lineage_object_id=p_root_id AND p.tenant_id=root_row.tenant_id AND p.deal_id=root_row.deal_id AND p.source_binding_state='BOUND'; manifest:=package_row.metadata->'source_manifest'; IF pg_catalog.jsonb_typeof(manifest)<>'object' OR pg_catalog.jsonb_typeof(manifest->'files')<>'array' THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; SELECT pg_catalog.jsonb_agg( pg_catalog.jsonb_build_array( (item->>'sequence')::integer, item->>'source_locator_sha256', item->>'content_sha256', (item->>'size_bytes')::bigint, item->>'extension', (item->>'document_id')::uuid::text, (item->>'doc_id')::uuid::text ) ORDER BY ordinal ) INTO manifest_members FROM pg_catalog.jsonb_array_elements(manifest->'files') WITH ORDINALITY AS members(item,ordinal); expected_version:='source-package-manifest-v1:'||(manifest->>'manifest_sha256'); expected_disposition:='CORPUS'; expected_identity:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'source-package-identity-v1', package_row.package_id::text, manifest->>'schema_version', manifest->>'manifest_sha256', manifest->>'source_tree_fingerprint', (manifest->>'file_count')::integer, (manifest->>'total_bytes')::bigint, manifest_members ) ); expected_content:=manifest->>'manifest_sha256'; expected_corpus:=manifest->>'manifest_sha256'; native_scope:=pg_catalog.jsonb_build_array( root_row.tenant_id::text, root_row.deal_id::text, package_row.package_id::text ); ELSE IF root_row.object_kind='PACKAGE_FILE' THEN SELECT f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence, f.path_hash,f.extension,f.sha256,f.file_status,f.support_status, f.triage_status,f.parse_status,f.reason_codes,f.error_codes, f.doc_id,f.document_id,f.storage_uri,f.source_binding_state, f.source_lineage_object_id,f.source_corpus_receipt_id INTO STRICT file_row FROM public.data_room_package_files AS f WHERE f.source_lineage_object_id=p_root_id AND f.tenant_id=root_row.tenant_id AND f.deal_id=root_row.deal_id AND f.source_binding_state='BOUND'; ELSIF root_row.object_kind='ARTIFACT' THEN SELECT a.doc_id,a.tenant_id,a.deal_id,a.doc_type,a.title, a.source_system,a.version_id,a.ingested_at,a.sha256,a.uri, a.metadata,a.source_binding_state,a.source_lineage_object_id, a.source_corpus_receipt_id INTO STRICT artifact_row FROM public.document_artifacts AS a WHERE a.source_lineage_object_id=p_root_id AND a.tenant_id=root_row.tenant_id AND a.deal_id=root_row.deal_id AND a.source_binding_state='BOUND'; SELECT f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence, f.path_hash,f.extension,f.sha256,f.file_status,f.support_status, f.triage_status,f.parse_status,f.reason_codes,f.error_codes, f.doc_id,f.document_id,f.storage_uri,f.source_binding_state, f.source_lineage_object_id,f.source_corpus_receipt_id INTO STRICT file_row FROM public.data_room_package_files AS f WHERE f.tenant_id=root_row.tenant_id AND f.deal_id=root_row.deal_id AND f.package_id=root_row.corpus_receipt_id AND f.doc_id=artifact_row.doc_id AND f.source_binding_state='BOUND'; ELSIF root_row.object_kind='DOCUMENT' THEN SELECT d.document_id,d.tenant_id,d.deal_id,d.doc_id,d.doc_type, d.parse_status,d.metadata,d.source_binding_state, d.source_lineage_object_id,d.source_corpus_receipt_id INTO STRICT document_row FROM public.documents AS d WHERE d.source_lineage_object_id=p_root_id AND d.tenant_id=root_row.tenant_id AND d.deal_id=root_row.deal_id AND d.source_binding_state='BOUND'; SELECT f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence, f.path_hash,f.extension,f.sha256,f.file_status,f.support_status, f.triage_status,f.parse_status,f.reason_codes,f.error_codes, f.doc_id,f.document_id,f.storage_uri,f.source_binding_state, f.source_lineage_object_id,f.source_corpus_receipt_id INTO STRICT file_row FROM public.data_room_package_files AS f WHERE f.tenant_id=root_row.tenant_id AND f.deal_id=root_row.deal_id AND f.package_id=root_row.corpus_receipt_id AND f.document_id=document_row.document_id AND f.source_binding_state='BOUND'; ELSIF root_row.object_kind='PARSE_ATTEMPT' THEN SELECT a.parse_attempt_id,a.tenant_id,a.deal_id,a.document_id, a.attempt_ordinal,a.parser_name,a.parser_version,a.input_sha256, a.receipt_sha256,a.status,a.started_at,a.finished_at, a.source_binding_state,a.source_lineage_object_id, a.source_corpus_receipt_id,a.write_origin INTO STRICT attempt_row FROM public.document_parse_attempts AS a WHERE a.source_lineage_object_id=p_root_id AND a.tenant_id=root_row.tenant_id AND a.deal_id=root_row.deal_id AND a.source_binding_state='BOUND'; SELECT d.document_id,d.tenant_id,d.deal_id,d.doc_id,d.doc_type, d.parse_status,d.metadata,d.source_binding_state, d.source_lineage_object_id,d.source_corpus_receipt_id INTO STRICT document_row FROM public.documents AS d WHERE d.tenant_id=root_row.tenant_id AND d.deal_id=root_row.deal_id AND d.document_id=attempt_row.document_id AND d.source_corpus_receipt_id=root_row.corpus_receipt_id AND d.source_binding_state='BOUND'; SELECT f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence, f.path_hash,f.extension,f.sha256,f.file_status,f.support_status, f.triage_status,f.parse_status,f.reason_codes,f.error_codes, f.doc_id,f.document_id,f.storage_uri,f.source_binding_state, f.source_lineage_object_id,f.source_corpus_receipt_id INTO STRICT file_row FROM public.data_room_package_files AS f WHERE f.tenant_id=root_row.tenant_id AND f.deal_id=root_row.deal_id AND f.package_id=root_row.corpus_receipt_id AND f.document_id=document_row.document_id AND f.source_binding_state='BOUND'; ELSIF root_row.object_kind='SPAN' THEN SELECT s.span_id,s.tenant_id,s.deal_id,s.document_id,s.span_type, s.locator,s.content_hash,s.parse_attempt_id, s.source_binding_state,s.source_lineage_object_id, s.source_corpus_receipt_id INTO STRICT span_row FROM public.document_spans AS s WHERE s.source_lineage_object_id=p_root_id AND s.tenant_id=root_row.tenant_id AND s.deal_id=root_row.deal_id AND s.source_binding_state='BOUND'; SELECT a.parse_attempt_id,a.tenant_id,a.deal_id,a.document_id, a.attempt_ordinal,a.parser_name,a.parser_version,a.input_sha256, a.receipt_sha256,a.status,a.started_at,a.finished_at, a.source_binding_state,a.source_lineage_object_id, a.source_corpus_receipt_id,a.write_origin INTO STRICT attempt_row FROM public.document_parse_attempts AS a WHERE a.tenant_id=root_row.tenant_id AND a.deal_id=root_row.deal_id AND a.parse_attempt_id=span_row.parse_attempt_id AND a.source_corpus_receipt_id=root_row.corpus_receipt_id AND a.source_binding_state='BOUND'; SELECT d.document_id,d.tenant_id,d.deal_id,d.doc_id,d.doc_type, d.parse_status,d.metadata,d.source_binding_state, d.source_lineage_object_id,d.source_corpus_receipt_id INTO STRICT document_row FROM public.documents AS d WHERE d.tenant_id=root_row.tenant_id AND d.deal_id=root_row.deal_id AND d.document_id=span_row.document_id AND d.source_corpus_receipt_id=root_row.corpus_receipt_id AND d.source_binding_state='BOUND'; SELECT f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence, f.path_hash,f.extension,f.sha256,f.file_status,f.support_status, f.triage_status,f.parse_status,f.reason_codes,f.error_codes, f.doc_id,f.document_id,f.storage_uri,f.source_binding_state, f.source_lineage_object_id,f.source_corpus_receipt_id INTO STRICT file_row FROM public.data_room_package_files AS f WHERE f.tenant_id=root_row.tenant_id AND f.deal_id=root_row.deal_id AND f.package_id=root_row.corpus_receipt_id AND f.document_id=document_row.document_id AND f.source_binding_state='BOUND'; ELSE RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; SELECT p.package_id,p.tenant_id,p.deal_id,p.status,p.metadata, p.source_binding_state,p.source_lineage_object_id INTO STRICT package_row FROM public.data_room_packages AS p WHERE p.tenant_id=root_row.tenant_id AND p.deal_id=root_row.deal_id AND p.package_id=root_row.corpus_receipt_id AND p.source_binding_state='BOUND'; manifest:=package_row.metadata->'source_manifest'; IF pg_catalog.jsonb_typeof(manifest)<>'object' OR pg_catalog.jsonb_typeof(manifest->'files')<>'array' THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; SELECT item INTO STRICT manifest_file FROM pg_catalog.jsonb_array_elements(manifest->'files') AS files(item) WHERE (item->>'sequence')::integer=file_row.sequence; IF manifest_file->>'source_locator_sha256' IS DISTINCT FROM file_row.path_hash OR manifest_file->>'content_sha256' IS DISTINCT FROM file_row.sha256 OR manifest_file->>'extension' IS DISTINCT FROM file_row.extension OR (manifest_file->>'document_id')::uuid IS DISTINCT FROM file_row.document_id OR (manifest_file->>'doc_id')::uuid IS DISTINCT FROM file_row.doc_id THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; IF document_row IS NULL THEN SELECT d.document_id,d.tenant_id,d.deal_id,d.doc_id,d.doc_type, d.parse_status,d.metadata,d.source_binding_state, d.source_lineage_object_id,d.source_corpus_receipt_id INTO STRICT document_row FROM public.documents AS d WHERE d.tenant_id=root_row.tenant_id AND d.deal_id=root_row.deal_id AND d.document_id=file_row.document_id AND d.source_corpus_receipt_id=root_row.corpus_receipt_id AND d.source_binding_state='BOUND'; END IF; IF attempt_row IS NULL THEN SELECT count(*) INTO matching_count FROM public.document_parse_attempts AS a WHERE a.tenant_id=root_row.tenant_id AND a.deal_id=root_row.deal_id AND a.document_id=document_row.document_id AND a.source_corpus_receipt_id=root_row.corpus_receipt_id AND a.source_binding_state='BOUND'; IF matching_count<>1 THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; SELECT a.parse_attempt_id,a.tenant_id,a.deal_id,a.document_id, a.attempt_ordinal,a.parser_name,a.parser_version,a.input_sha256, a.receipt_sha256,a.status,a.started_at,a.finished_at, a.source_binding_state,a.source_lineage_object_id, a.source_corpus_receipt_id,a.write_origin INTO STRICT attempt_row FROM public.document_parse_attempts AS a WHERE a.tenant_id=root_row.tenant_id AND a.deal_id=root_row.deal_id AND a.document_id=document_row.document_id AND a.source_corpus_receipt_id=root_row.corpus_receipt_id AND a.source_binding_state='BOUND'; END IF; metadata:=document_row.metadata; support_status:=CASE WHEN metadata->>'parser_support_status' IN ( 'supported','partially_supported','unsupported','encrypted', 'scanned_or_image_only','too_large','corrupted', 'conversion_required','excluded_confirmed','unknown' ) THEN metadata->>'parser_support_status' ELSE 'unknown' END; triage_status:=CASE WHEN metadata->>'parser_triage_status' IN ( 'ready','partial','blocked','conversion_required', 'unsupported_source','ocr_required','too_large', 'excluded_confirmed','unknown' ) THEN metadata->>'parser_triage_status' ELSE 'unknown' END; schema_refs:=COALESCE(metadata->'applicable_schema_refs','[]'::jsonb); IF pg_catalog.jsonb_typeof(schema_refs)<>'array' THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; terminal_projection:=pg_catalog.jsonb_build_object( 'parser_support_status',support_status, 'parser_triage_status',triage_status, 'applicable_schema_refs',schema_refs ); terminal_receipt:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'terminal-document-metadata-v2', document_row.doc_type, document_row.parse_status, terminal_projection ) ); expected_disposition:=CASE document_row.parse_status WHEN 'PARSED' THEN 'EVIDENCE_BEARING' WHEN 'FAILED' THEN 'CONFIRMED_EXCLUSION' ELSE NULL END; IF expected_disposition IS NULL OR attempt_row.receipt_sha256 IS DISTINCT FROM terminal_receipt OR attempt_row.input_sha256 IS DISTINCT FROM manifest_file->>'content_sha256' OR attempt_row.status IS DISTINCT FROM document_row.parse_status OR attempt_row.write_origin<>'RUNTIME' THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; IF root_row.object_kind='PACKAGE_FILE' THEN storage_uri_sha:=CASE WHEN file_row.storage_uri IS NULL THEN NULL ELSE pg_catalog.encode( pg_catalog.sha256( pg_catalog.convert_to(file_row.storage_uri,'UTF8') ), 'hex' ) END; expected_version:= 'source-package-file-v1:' ||(manifest->>'manifest_sha256') ||':' ||file_row.sequence::text; expected_identity:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'source-package-file-identity-v1', package_row.package_id::text, file_row.file_entry_id::text, file_row.sequence, file_row.path_hash, file_row.extension, file_row.sha256, (manifest_file->>'size_bytes')::bigint, file_row.document_id::text, file_row.doc_id::text, file_row.file_status, file_row.support_status, file_row.triage_status, file_row.parse_status, file_row.reason_codes, file_row.error_codes, storage_uri_sha, expected_disposition, terminal_receipt ) ); native_scope:=pg_catalog.jsonb_build_array( root_row.tenant_id::text, root_row.deal_id::text, package_row.package_id::text, file_row.file_entry_id::text ); ELSIF root_row.object_kind='ARTIFACT' THEN IF artifact_row IS NULL THEN SELECT a.doc_id,a.tenant_id,a.deal_id,a.doc_type,a.title, a.source_system,a.version_id,a.ingested_at,a.sha256,a.uri, a.metadata,a.source_binding_state,a.source_lineage_object_id, a.source_corpus_receipt_id INTO STRICT artifact_row FROM public.document_artifacts AS a WHERE a.tenant_id=root_row.tenant_id AND a.deal_id=root_row.deal_id AND a.doc_id=file_row.doc_id AND a.source_corpus_receipt_id=root_row.corpus_receipt_id AND a.source_binding_state='BOUND'; END IF; integrity:=artifact_row.metadata->'durable_object_integrity'; IF pg_catalog.jsonb_typeof(integrity)<>'object' OR ( SELECT count(*) FROM pg_catalog.jsonb_object_keys(integrity) )<>8 OR NOT integrity ?& ARRAY[ 'schema_version','doc_id','content_sha256','size_bytes', 'uri_sha256','version_id','backend','streaming_verified' ] THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; integrity:=pg_catalog.jsonb_build_object( 'schema_version',integrity->>'schema_version', 'doc_id',(integrity->>'doc_id')::uuid::text, 'content_sha256',integrity->>'content_sha256', 'size_bytes',(integrity->>'size_bytes')::bigint, 'uri_sha256',integrity->>'uri_sha256', 'version_id',(integrity->>'version_id')::uuid::text, 'backend',integrity->>'backend', 'streaming_verified',(integrity->>'streaming_verified')::boolean ); IF artifact_row.uri IS NULL OR artifact_row.sha256 IS DISTINCT FROM manifest_file->>'content_sha256' OR integrity->>'schema_version'<>'durable-object-integrity-v1' OR (integrity->>'doc_id')::uuid IS DISTINCT FROM artifact_row.doc_id OR integrity->>'content_sha256' IS DISTINCT FROM artifact_row.sha256 OR (integrity->>'size_bytes')::bigint IS DISTINCT FROM (manifest_file->>'size_bytes')::bigint OR integrity->>'uri_sha256' IS DISTINCT FROM pg_catalog.encode( pg_catalog.sha256(pg_catalog.convert_to(artifact_row.uri,'UTF8')), 'hex' ) OR (integrity->>'version_id')::uuid IS DISTINCT FROM artifact_row.version_id::uuid OR integrity->>'backend' NOT LIKE 'compliant:%' OR (integrity->>'streaming_verified')::boolean IS NOT TRUE THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; artifact_metadata_sha:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'document-artifact-metadata-v2', pg_catalog.encode( pg_catalog.sha256( pg_catalog.convert_to(artifact_row.title,'UTF8') ), 'hex' ), artifact_row.source_system, artifact_row.doc_type, artifact_row.version_id::uuid::text, integrity ) ); expected_version:= 'document-artifact-v1:'||artifact_row.version_id::uuid::text; expected_identity:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'document-artifact-identity-v1', artifact_row.doc_id::text, artifact_row.doc_type, artifact_row.source_system, artifact_row.version_id::uuid::text, pg_catalog.to_char( artifact_row.ingested_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"' ), manifest_file->>'content_sha256', pg_catalog.encode( pg_catalog.sha256( pg_catalog.convert_to(artifact_row.uri,'UTF8') ), 'hex' ), artifact_metadata_sha ) ); native_scope:=pg_catalog.jsonb_build_array( root_row.tenant_id::text, root_row.deal_id::text, artifact_row.doc_id::text ); ELSIF root_row.object_kind='DOCUMENT' THEN expected_version:='parsed-document-v1:'||terminal_receipt; expected_identity:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'parsed-document-identity-v1', document_row.document_id::text, document_row.doc_id::text, document_row.doc_type, document_row.parse_status, terminal_receipt, attempt_row.parse_attempt_id::text, attempt_row.receipt_sha256 ) ); native_scope:=pg_catalog.jsonb_build_array( root_row.tenant_id::text, root_row.deal_id::text, document_row.document_id::text ); ELSIF root_row.object_kind='PARSE_ATTEMPT' THEN expected_version:= 'parser-attempt-v1:' ||attempt_row.parser_name ||':' ||attempt_row.parser_version; expected_identity:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'parse-attempt-identity-v1', attempt_row.parse_attempt_id::text, attempt_row.document_id::text, attempt_row.attempt_ordinal, attempt_row.parser_name, attempt_row.parser_version, attempt_row.input_sha256, attempt_row.receipt_sha256, attempt_row.status, pg_catalog.to_char( attempt_row.started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"' ), pg_catalog.to_char( attempt_row.finished_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"' ) ) ); native_scope:=pg_catalog.jsonb_build_array( root_row.tenant_id::text, root_row.deal_id::text, attempt_row.document_id::text, attempt_row.parse_attempt_id::text ); ELSE locator_sha:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'span-locator-v1', span_row.span_type, span_row.locator ) ); expected_version:= 'document-span-v1:' ||attempt_row.parse_attempt_id::text ||':' ||span_row.span_type; expected_disposition:='EVIDENCE_BEARING'; expected_identity:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'document-span-identity-v1', span_row.span_id::text, span_row.document_id::text, attempt_row.parse_attempt_id::text, span_row.span_type, locator_sha, span_row.content_hash ) ); expected_content:=span_row.content_hash; native_scope:=pg_catalog.jsonb_build_array( root_row.tenant_id::text, root_row.deal_id::text, span_row.document_id::text, span_row.span_id::text ); END IF; IF root_row.object_kind<>'SPAN' THEN expected_content:=manifest_file->>'content_sha256'; END IF; expected_corpus:=manifest->>'manifest_sha256'; END IF; name_hash:=public.idis_task7_source_hash_jsonb_v1( pg_catalog.jsonb_build_array( 'source-lineage-object-v2', root_row.tenant_id::text, root_row.deal_id::text, root_row.corpus_receipt_id::text, root_row.object_kind, native_scope, expected_version, expected_disposition, expected_identity, expected_content, expected_corpus ) ); expected_root_id:=public.idis_task7_uuidv5_v1( '90a5de51-2753-5547-9375-ac5312629947'::uuid, name_hash ); RETURN pg_catalog.jsonb_build_object( 'source_lineage_object_id',expected_root_id::text, 'tenant_id',root_row.tenant_id::text, 'deal_id',root_row.deal_id::text, 'corpus_receipt_id',root_row.corpus_receipt_id::text, 'object_kind',root_row.object_kind, 'source_version',expected_version, 'source_disposition',expected_disposition, 'object_identity_sha256',expected_identity, 'content_sha256',expected_content, 'corpus_receipt_sha256',expected_corpus, 'write_origin','RUNTIME' ); EXCEPTION WHEN no_data_found OR too_many_rows OR invalid_text_representation OR numeric_value_out_of_range THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END $$",
    "CREATE FUNCTION public.idis_task7_current_source_exact_one_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ DECLARE scope_tenant uuid; scope_deal uuid; BEGIN IF TG_TABLE_NAME='source_lineage_objects' AND TG_OP='INSERT' AND pg_catalog.to_jsonb(NEW)->>'write_origin'='BACKFILL' THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; IF TG_TABLE_NAME='source_lineage_objects' AND TG_OP='UPDATE' THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; IF TG_TABLE_NAME='document_parse_attempts' AND TG_OP IN ('INSERT','UPDATE') AND pg_catalog.to_jsonb(NEW)->>'write_origin'='BACKFILL' THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; IF TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; scope_deal:=OLD.deal_id; ELSE scope_tenant:=NEW.tenant_id; scope_deal:=NEW.deal_id; END IF; IF EXISTS ( WITH roots AS ( SELECT source_lineage_object_id AS root_id, object_kind, corpus_receipt_id FROM public.source_lineage_objects WHERE tenant_id=scope_tenant AND deal_id=scope_deal ), natives AS ( SELECT source_lineage_object_id AS root_id, source_lineage_object_kind AS object_kind, source_corpus_receipt_id AS corpus_receipt_id FROM public.data_room_packages WHERE tenant_id=scope_tenant AND deal_id=scope_deal AND source_binding_state='BOUND' UNION ALL SELECT source_lineage_object_id, source_lineage_object_kind, source_corpus_receipt_id FROM public.data_room_package_files WHERE tenant_id=scope_tenant AND deal_id=scope_deal AND source_binding_state='BOUND' UNION ALL SELECT source_lineage_object_id, source_lineage_object_kind, source_corpus_receipt_id FROM public.document_artifacts WHERE tenant_id=scope_tenant AND deal_id=scope_deal AND source_binding_state='BOUND' UNION ALL SELECT source_lineage_object_id, source_lineage_object_kind, source_corpus_receipt_id FROM public.documents WHERE tenant_id=scope_tenant AND deal_id=scope_deal AND source_binding_state='BOUND' UNION ALL SELECT source_lineage_object_id, source_lineage_object_kind, source_corpus_receipt_id FROM public.document_parse_attempts WHERE tenant_id=scope_tenant AND deal_id=scope_deal AND source_binding_state='BOUND' UNION ALL SELECT source_lineage_object_id, source_lineage_object_kind, source_corpus_receipt_id FROM public.document_spans WHERE tenant_id=scope_tenant AND deal_id=scope_deal AND source_binding_state='BOUND' ), census AS ( SELECT COALESCE(roots.root_id,natives.root_id) AS root_id, count(roots.root_id) AS root_count, count(natives.root_id) AS native_count FROM roots FULL JOIN natives ON natives.root_id=roots.root_id AND natives.object_kind=roots.object_kind AND natives.corpus_receipt_id=roots.corpus_receipt_id GROUP BY COALESCE(roots.root_id,natives.root_id) ) SELECT 1 FROM census WHERE root_count<>1 OR native_count<>1 ) THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; IF EXISTS ( SELECT 1 FROM public.source_lineage_objects AS root WHERE root.tenant_id=scope_tenant AND root.deal_id=scope_deal AND root.write_origin='RUNTIME' AND public.idis_task7_source_projection_v1( root.source_lineage_object_id ) IS DISTINCT FROM pg_catalog.jsonb_build_object( 'source_lineage_object_id',root.source_lineage_object_id::text, 'tenant_id',root.tenant_id::text, 'deal_id',root.deal_id::text, 'corpus_receipt_id',root.corpus_receipt_id::text, 'object_kind',root.object_kind, 'source_version',root.source_version, 'source_disposition',root.source_disposition, 'object_identity_sha256',root.object_identity_sha256, 'content_sha256',root.content_sha256, 'corpus_receipt_sha256',root.corpus_receipt_sha256, 'write_origin',root.write_origin ) ) THEN RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514'; END IF; RETURN NULL; END $$",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_source_lineage_objects_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.source_lineage_objects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_data_room_packages_source_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.data_room_packages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_data_room_package_files_source_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.data_room_package_files DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_document_artifacts_source_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.document_artifacts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_documents_source_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.documents DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_document_parse_attempts_source_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.document_parse_attempts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_document_spans_source_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.document_spans DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_exact_one_v1()",
    "REVOKE ALL ON FUNCTION public.idis_task7_source_canonical_jsonb_text_v1(jsonb) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_source_hash_jsonb_v1(jsonb) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_sha1_v1(bytea) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_uuidv5_v1(uuid,text) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_source_projection_v1(uuid) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_source_exact_one_v1() FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_canonical_jsonb_text_v1(p_value jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$DECLARE kind text; rendered text; BEGIN kind:=pg_catalog.jsonb_typeof(p_value); IF kind='null' THEN RETURN 'null'; ELSIF kind='boolean' OR kind='number' THEN RETURN p_value::text; ELSIF kind='string' THEN RETURN pg_catalog.to_json(normalize(p_value#>>'{}',NFC))::text; ELSIF kind='array' THEN SELECT '['||COALESCE(pg_catalog.string_agg(public.idis_task7_canonical_jsonb_text_v1(item),',' ORDER BY ordinal),'')||']' INTO rendered FROM pg_catalog.jsonb_array_elements(p_value) WITH ORDINALITY AS values(item,ordinal); RETURN rendered; ELSIF kind='object' THEN SELECT '{'||COALESCE(pg_catalog.string_agg(pg_catalog.to_json(normalize(key,NFC))::text||':'||public.idis_task7_canonical_jsonb_text_v1(value),',' ORDER BY normalize(key,NFC) COLLATE \"C\"),'')||'}' INTO rendered FROM pg_catalog.jsonb_each(p_value); RETURN rendered; END IF; RAISE EXCEPTION 'PIPELINE_CONTRACT_HASH_MISMATCH' USING ERRCODE='23514'; END$$",
    "CREATE FUNCTION public.idis_task7_hash_jsonb_v1(p_value jsonb) RETURNS text LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(public.idis_task7_canonical_jsonb_text_v1(p_value),'UTF8')),'hex')$$",
    "CREATE FUNCTION public.idis_task7_current_native_hashes_v1(p_tenant_id uuid,p_deal_id uuid,p_run_id uuid,p_output_object_id uuid) RETURNS TABLE(output_kind text,expected_native_identity_sha256 text,expected_native_output_sha256 text) LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$WITH roots AS (SELECT r.output_kind,r.pipeline_contract_version,r.pipeline_contract_hash FROM public.run_output_objects r WHERE r.tenant_id=p_tenant_id AND r.deal_id=p_deal_id AND r.run_id=p_run_id AND r.output_object_id=p_output_object_id AND r.output_class='NATIVE_ROW'),native_rows AS (SELECT c.run_output_object_kind AS output_kind,pg_catalog.jsonb_build_array(c.coverage_record_id) AS nk,pg_catalog.jsonb_build_array(c.selection_id::text,c.methodology_version_record_id::text,c.methodology_version_id,c.methodology_question_record_id::text,c.methodology_question_id,c.methodology_type) AS payload FROM public.methodology_coverage_records c WHERE c.tenant_id=p_tenant_id AND c.deal_id=p_deal_id AND c.run_id=p_run_id AND c.run_output_object_id=p_output_object_id UNION ALL SELECT t.run_output_object_kind,pg_catalog.jsonb_build_array(t.task_record_id::text),pg_catalog.jsonb_build_array(t.selection_id::text,t.coverage_record_id,t.methodology_version_record_id::text,t.methodology_version_id,t.methodology_question_record_id::text,t.methodology_question_id,t.methodology_type,t.extraction_task_id,t.document_id::text,t.document_classification_id,t.document_classification_sha256,t.planner_contract_version,t.task_contract_version,t.task_input_sha256,t.candidate_span_set_sha256,COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(s.span_ordinal,s.task_span_record_id::text,s.document_id::text,s.span_id::text,s.content_sha256,s.locator_sha256) ORDER BY s.span_ordinal,s.task_span_record_id) FROM public.methodology_extraction_task_spans s WHERE s.tenant_id=t.tenant_id AND s.deal_id=t.deal_id AND s.run_id=t.run_id AND s.task_record_id=t.task_record_id),'[]'::jsonb)) FROM public.methodology_extraction_tasks t WHERE t.tenant_id=p_tenant_id AND t.deal_id=p_deal_id AND t.run_id=p_run_id AND t.run_output_object_id=p_output_object_id UNION ALL SELECT r.run_output_object_kind,pg_catalog.jsonb_build_array(r.result_id::text),pg_catalog.jsonb_build_array(r.task_record_id::text,r.attempt_id::text,r.task_input_sha256,r.outcome,r.result_sha256,to_jsonb(ARRAY(SELECT value::text FROM pg_catalog.unnest(r.searched_document_ids) value ORDER BY value::text COLLATE \"C\")),to_jsonb(ARRAY(SELECT value::text FROM pg_catalog.unnest(r.searched_span_ids) value ORDER BY value::text COLLATE \"C\")),r.searched_scope_sha256,r.provider_provenance_sha256,r.gap_finding) FROM public.methodology_extraction_results r WHERE r.tenant_id=p_tenant_id AND r.deal_id=p_deal_id AND r.run_id=p_run_id AND r.run_output_object_id=p_output_object_id UNION ALL SELECT f.run_output_object_kind,pg_catalog.jsonb_build_array(f.factual_output_record_id::text),pg_catalog.jsonb_build_array(f.result_id::text,f.task_record_id::text,f.output_ordinal,f.factual_output_id::text,f.typed_output_sha256,COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(span_id::text,locator_sha256) ORDER BY span_id::text COLLATE \"C\",locator_sha256 COLLATE \"C\") FROM ROWS FROM (pg_catalog.unnest(f.accepted_source_span_ids), pg_catalog.unnest(f.accepted_locator_sha256s)) AS zipped(span_id,locator_sha256)),'[]'::jsonb),f.accepted_locator_set_sha256) FROM public.methodology_result_factual_outputs f WHERE f.tenant_id=p_tenant_id AND f.deal_id=p_deal_id AND f.run_id=p_run_id AND f.run_output_object_id=p_output_object_id UNION ALL SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array(l.claim_link_id::text),pg_catalog.jsonb_build_array(l.result_id::text,l.factual_output_record_id::text,l.selection_id::text,l.coverage_record_id,l.methodology_version_record_id::text,l.methodology_question_record_id::text,l.claim_id::text,l.canonical_key_sha256,l.reused_existing_claim) FROM public.methodology_result_claim_links l WHERE l.tenant_id=p_tenant_id AND l.deal_id=p_deal_id AND l.run_id=p_run_id AND l.run_output_object_id=p_output_object_id UNION ALL SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array(l.evidence_link_id::text),pg_catalog.jsonb_build_array(l.result_id::text,l.factual_output_record_id::text,l.claim_link_id::text,l.evidence_id::text,l.document_id::text,l.source_span_id::text,l.locator_sha256) FROM public.methodology_result_evidence_links l WHERE l.tenant_id=p_tenant_id AND l.deal_id=p_deal_id AND l.run_id=p_run_id AND l.run_output_object_id=p_output_object_id UNION ALL SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array(l.sanad_link_id::text),pg_catalog.jsonb_build_array(l.result_id::text,l.factual_output_record_id::text,l.claim_link_id::text,l.sanad_id::text,l.primary_evidence_id::text) FROM public.methodology_result_sanad_links l WHERE l.tenant_id=p_tenant_id AND l.deal_id=p_deal_id AND l.run_id=p_run_id AND l.run_output_object_id=p_output_object_id UNION ALL SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array(l.defect_link_id::text),pg_catalog.jsonb_build_array(l.result_id::text,l.factual_output_record_id::text,l.sanad_link_id::text,l.defect_id::text) FROM public.methodology_result_defect_links l WHERE l.tenant_id=p_tenant_id AND l.deal_id=p_deal_id AND l.run_id=p_run_id AND l.run_output_object_id=p_output_object_id UNION ALL SELECT c.run_output_object_kind,pg_catalog.jsonb_build_array(c.calc_id::text),pg_catalog.jsonb_build_array(c.calc_type,c.inputs,c.output,c.formula_hash,c.code_version,c.reproducibility_hash,(SELECT pg_catalog.jsonb_build_array(s.calc_sanad_id::text,COALESCE((SELECT pg_catalog.jsonb_agg(value ORDER BY public.idis_task7_canonical_jsonb_text_v1(value) COLLATE \"C\") FROM pg_catalog.jsonb_array_elements(s.input_claim_ids) value),'[]'::jsonb),s.input_min_sanad_grade,s.calc_grade,s.explanation) FROM public.calc_sanads s WHERE s.tenant_id=c.tenant_id AND s.deal_id=c.deal_id AND s.run_id=c.run_id AND s.calc_id=c.calc_id)) FROM public.deterministic_calculations c WHERE c.tenant_id=p_tenant_id AND c.deal_id=p_deal_id AND c.run_id=p_run_id AND c.run_output_object_id=p_output_object_id UNION ALL SELECT v.run_output_object_kind,pg_catalog.jsonb_build_array(v.embedding_id::text),pg_catalog.jsonb_build_array(v.source_type,v.source_id::text,v.embedding_model,v.embedding_dimensions,v.content_hash,pg_catalog.encode(pg_catalog.sha256(public.vector_send(v.embedding)),'hex')) FROM public.vector_embeddings v WHERE v.tenant_id=p_tenant_id AND v.deal_id=p_deal_id AND v.run_id=p_run_id AND v.run_output_object_id=p_output_object_id UNION ALL SELECT f.run_output_object_kind,pg_catalog.jsonb_build_array(f.tenant_id::text,f.run_id::text,f.finding_id),pg_catalog.jsonb_build_array(f.court_id::text,f.finding_type,f.claim_id,COALESCE((SELECT pg_catalog.jsonb_agg(value ORDER BY public.idis_task7_canonical_jsonb_text_v1(value) COLLATE \"C\") FROM pg_catalog.jsonb_array_elements(f.evidence_ids) value),'[]'::jsonb),f.sanad_id::text,COALESCE((SELECT pg_catalog.jsonb_agg(value ORDER BY public.idis_task7_canonical_jsonb_text_v1(value) COLLATE \"C\") FROM pg_catalog.jsonb_array_elements(f.calc_ids) value),'[]'::jsonb),COALESCE((SELECT pg_catalog.jsonb_agg(value ORDER BY public.idis_task7_canonical_jsonb_text_v1(value) COLLATE \"C\") FROM pg_catalog.jsonb_array_elements(f.defect_ids) value),'[]'::jsonb),COALESCE((SELECT pg_catalog.jsonb_agg(value ORDER BY public.idis_task7_canonical_jsonb_text_v1(value) COLLATE \"C\") FROM pg_catalog.jsonb_array_elements(f.reason_codes) value),'[]'::jsonb)) FROM public.evidence_trust_findings f WHERE f.tenant_id=p_tenant_id AND f.deal_id=p_deal_id AND f.run_id=p_run_id AND f.run_output_object_id=p_output_object_id UNION ALL SELECT m.run_output_object_kind,pg_catalog.jsonb_build_array(m.record_id::text),pg_catalog.jsonb_build_array('muhasabah-native','task7-muhasabah-native-v1','a962a618cf9032c762f2a6f364168fd4d1d542e817429da69bc5cdbf41159d00',pg_catalog.jsonb_build_object('tenant_id',m.tenant_id::text,'deal_id',m.deal_id::text,'run_id',m.run_id::text,'record_id',m.record_id::text,'source_step',m.source_step,'agent_id',m.agent_id,'output_id',m.output_id,'confidence',m.confidence,'is_subjective',m.is_subjective,'supported_claim_ids',m.supported_claim_ids,'supported_calc_ids',m.supported_calc_ids,'uncertainties',m.uncertainties,'record_timestamp',m.record_timestamp)) FROM public.muhasabah_records m WHERE m.tenant_id=p_tenant_id AND m.deal_id=p_deal_id AND m.run_id=p_run_id AND m.run_output_object_id=p_output_object_id UNION ALL SELECT v.run_output_object_kind,pg_catalog.jsonb_build_array(v.package_id::text),pg_catalog.jsonb_build_array('validated-evidence-package-native','task7-validated-evidence-package-native-v1','cdcbc10cceae5b7bb354ad27aa75817890fe7a77a6f392ce587e77550235f391',pg_catalog.jsonb_build_object('tenant_id',v.tenant_id::text,'deal_id',v.deal_id::text,'run_id',v.run_id::text,'package_id',v.package_id::text,'court_id',v.court_id::text,'dashboard_id',v.dashboard_id::text,'status',v.status,'safe_summary',v.safe_summary)) FROM public.validated_evidence_packages v WHERE v.tenant_id=p_tenant_id AND v.deal_id=p_deal_id AND v.run_id=p_run_id AND v.run_output_object_id=p_output_object_id UNION ALL SELECT d.run_output_object_kind,pg_catalog.jsonb_build_array(d.debate_id::text),pg_catalog.jsonb_build_array('debate-session-native','task7-debate-session-native-v1','9e1b7786d6e35e482422149e8ec308c76cd9f2581924f23a7fc7ba7b52bc3601',pg_catalog.jsonb_build_object('debate_id',d.debate_id::text,'tenant_id',d.tenant_id::text,'deal_id',d.deal_id::text,'run_id',d.run_id::text,'protocol_version',d.protocol_version,'max_rounds',d.max_rounds,'rounds',d.rounds,'status',d.status)) FROM public.debate_sessions d WHERE d.tenant_id=p_tenant_id AND d.deal_id=p_deal_id AND d.run_id=p_run_id AND d.run_output_object_id=p_output_object_id UNION ALL SELECT l.run_output_object_kind,pg_catalog.jsonb_build_array(l.challenge_id::text),pg_catalog.jsonb_build_array('layer2-challenge-native','task7-layer2-challenge-native-v1','5ccd3d54e892646e5b2c4b58fbaf62922bcb3aacc13aa8814f79d1fed60f0fed',pg_catalog.jsonb_build_object('tenant_id',l.tenant_id::text,'deal_id',l.deal_id::text,'run_id',l.run_id::text,'challenge_id',l.challenge_id::text,'source_debate_id',l.source_debate_id,'status',l.status,'safe_summary',l.safe_summary)) FROM public.layer2_ic_challenges l WHERE l.tenant_id=p_tenant_id AND l.deal_id=p_deal_id AND l.run_id=p_run_id AND l.run_output_object_id=p_output_object_id UNION ALL SELECT f.run_output_object_kind,pg_catalog.jsonb_build_array(f.tenant_id::text,f.run_id::text,f.finding_id),pg_catalog.jsonb_build_array(f.challenge_id::text,f.finding_type,f.severity,f.category,f.supported_claim_ids,f.supported_calc_ids,f.graph_ref_ids,f.rag_ref_ids,f.enrichment_ref_ids) FROM public.layer2_ic_findings f WHERE f.tenant_id=p_tenant_id AND f.deal_id=p_deal_id AND f.run_id=p_run_id AND f.run_output_object_id=p_output_object_id UNION ALL SELECT d.run_output_object_kind,pg_catalog.jsonb_build_array(d.deliverable_id::text),pg_catalog.jsonb_build_array(d.deliverable_type,d.format,d.status,d.uri,d.content_type,d.size_bytes,d.content_sha256,d.uri_sha256,d.object_version_hash,d.download_proof_hash,d.catalog_ordinal) FROM public.deliverables d WHERE d.tenant_id=p_tenant_id AND d.deal_id=p_deal_id AND d.run_id=p_run_id AND d.run_output_object_id=p_output_object_id),resolved AS (SELECT r.output_kind,r.pipeline_contract_version,r.pipeline_contract_hash,n.nk,n.payload FROM roots r JOIN native_rows n ON n.output_kind=r.output_kind) SELECT output_kind,public.idis_task7_hash_jsonb_v1(pg_catalog.jsonb_build_array('native-identity-v1',p_tenant_id::text,p_deal_id::text,p_run_id::text,output_kind,nk)),public.idis_task7_hash_jsonb_v1(pg_catalog.jsonb_build_array('native-output-v1',p_tenant_id::text,p_deal_id::text,p_run_id::text,pipeline_contract_version,pipeline_contract_hash,output_kind,nk,payload)) FROM resolved$$",
    "REVOKE ALL ON FUNCTION public.idis_task7_canonical_jsonb_text_v1(jsonb) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_hash_jsonb_v1(jsonb) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid) FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_current_output_exact_one_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$DECLARE scope_tenant uuid; scope_deal uuid; scope_run uuid; BEGIN IF TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; scope_deal:=OLD.deal_id; scope_run:=OLD.run_id; ELSE scope_tenant:=NEW.tenant_id; scope_deal:=NEW.deal_id; scope_run:=NEW.run_id; END IF; IF EXISTS (WITH roots AS (SELECT output_object_id AS root_id,output_kind FROM public.run_output_objects WHERE tenant_id=scope_tenant AND deal_id=scope_deal AND run_id=scope_run AND output_class='NATIVE_ROW'), natives AS (SELECT tenant_id,deal_id,run_id,run_output_object_id AS root_id,run_output_object_kind AS output_kind FROM public.methodology_coverage_records WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.methodology_extraction_tasks WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.methodology_extraction_results WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.methodology_result_factual_outputs WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.methodology_result_claim_links WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.methodology_result_evidence_links WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.methodology_result_sanad_links WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.methodology_result_defect_links WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.deterministic_calculations WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.vector_embeddings WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.evidence_trust_findings WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.muhasabah_records WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.validated_evidence_packages WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.debate_sessions WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.layer2_ic_challenges WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.layer2_ic_findings WHERE run_output_object_id IS NOT NULL UNION ALL SELECT tenant_id,deal_id,run_id,run_output_object_id,run_output_object_kind FROM public.deliverables WHERE run_output_object_id IS NOT NULL), scoped_natives AS (SELECT root_id,output_kind FROM natives WHERE tenant_id=scope_tenant AND deal_id=scope_deal AND run_id=scope_run), census AS (SELECT COALESCE(roots.root_id,scoped_natives.root_id) AS root_id,count(roots.root_id) AS root_count,count(scoped_natives.root_id) AS native_count FROM roots FULL JOIN scoped_natives ON scoped_natives.root_id=roots.root_id AND scoped_natives.output_kind=roots.output_kind GROUP BY COALESCE(roots.root_id,scoped_natives.root_id)) SELECT 1 FROM census WHERE root_count<>1 OR native_count<>1) THEN RAISE EXCEPTION 'STEP_OUTPUT_MISSING' USING ERRCODE='23514'; END IF; IF EXISTS (SELECT 1 FROM public.run_output_objects ro LEFT JOIN LATERAL public.idis_task7_current_native_hashes_v1(ro.tenant_id,ro.deal_id,ro.run_id,ro.output_object_id) expected ON true WHERE ro.tenant_id=scope_tenant AND ro.deal_id=scope_deal AND ro.run_id=scope_run AND ro.output_class='NATIVE_ROW' AND (expected.output_kind IS NULL OR expected.output_kind IS DISTINCT FROM ro.output_kind OR expected.expected_native_identity_sha256 IS DISTINCT FROM ro.native_identity_sha256 OR expected.expected_native_output_sha256 IS DISTINCT FROM ro.native_output_sha256)) THEN RAISE EXCEPTION 'STEP_OUTPUT_MISSING' USING ERRCODE='23514'; END IF; RETURN NULL; END$$",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_run_output_objects_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.run_output_objects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_methodology_coverage_records_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.methodology_coverage_records DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_methodology_extraction_tasks_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.methodology_extraction_tasks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_methodology_extraction_results_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.methodology_extraction_results DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_methodology_result_factual_outputs_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.methodology_result_factual_outputs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_methodology_result_claim_links_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.methodology_result_claim_links DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_methodology_result_evidence_links_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.methodology_result_evidence_links DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_methodology_result_sanad_links_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.methodology_result_sanad_links DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_methodology_result_defect_links_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.methodology_result_defect_links DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_deterministic_calculations_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.deterministic_calculations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_vector_embeddings_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.vector_embeddings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_evidence_trust_findings_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.evidence_trust_findings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_muhasabah_records_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.muhasabah_records DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_validated_evidence_packages_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.validated_evidence_packages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_debate_sessions_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.debate_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_layer2_ic_challenges_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.layer2_ic_challenges DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_layer2_ic_findings_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.layer2_ic_findings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_deliverables_output_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.deliverables DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_output_exact_one_v1() FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_manifest_reference_digest_v1(p_tenant_id uuid,p_deal_id uuid,p_run_id uuid,p_pipeline_contract_version text,p_pipeline_contract_hash text,p_manifest_deliverable_id uuid,p_member_kind text,p_catalog_ordinal integer,p_member_step_output_id uuid,p_member_output_object_id uuid,p_member_identity_sha256 text,p_member_value_sha256 text,p_member_content_sha256 text) RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$DECLARE preimage text; BEGIN preimage:='[\"deliverable-manifest-reference-digest-v1\",'||pg_catalog.to_json(normalize(p_tenant_id::text,NFC))::text||','||pg_catalog.to_json(normalize(p_deal_id::text,NFC))::text||','||pg_catalog.to_json(normalize(p_run_id::text,NFC))::text||','||pg_catalog.to_json(normalize(p_pipeline_contract_version,NFC))::text||','||pg_catalog.to_json(normalize(p_pipeline_contract_hash,NFC))::text||','||pg_catalog.to_json(normalize(p_manifest_deliverable_id::text,NFC))::text||','||pg_catalog.to_json(normalize(p_member_kind,NFC))::text||','||COALESCE(p_catalog_ordinal::text,'null')||','||pg_catalog.to_json(normalize(p_member_step_output_id::text,NFC))::text||','||pg_catalog.to_json(normalize(p_member_output_object_id::text,NFC))::text||','||pg_catalog.to_json(normalize(p_member_identity_sha256,NFC))::text||','||pg_catalog.to_json(normalize(p_member_value_sha256,NFC))::text||','||CASE WHEN p_member_content_sha256 IS NULL THEN 'null' ELSE pg_catalog.to_json(normalize(p_member_content_sha256,NFC))::text END||']'; RETURN pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(preimage,'UTF8')),'hex'); END$$",
    "REVOKE ALL ON FUNCTION public.idis_task7_manifest_reference_digest_v1(uuid,uuid,uuid,text,text,uuid,text,integer,uuid,uuid,text,text,text) FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_current_receipt_equality_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$DECLARE scope_tenant uuid; scope_deal uuid; scope_run uuid; BEGIN IF TG_OP='DELETE' THEN scope_tenant:=OLD.tenant_id; scope_run:=OLD.run_id; ELSE scope_tenant:=NEW.tenant_id; scope_run:=NEW.run_id; END IF; SELECT r.deal_id INTO scope_deal FROM public.runs r WHERE r.tenant_id=scope_tenant AND r.run_id=scope_run; IF scope_deal IS NULL THEN RAISE EXCEPTION 'CONSUMER_RECEIPT_INCOMPLETE' USING ERRCODE='23514'; END IF; IF EXISTS (SELECT 1 FROM public.run_steps s JOIN public.runs r ON r.tenant_id=s.tenant_id AND r.run_id=s.run_id WHERE s.tenant_id=scope_tenant AND r.deal_id=scope_deal AND s.run_id=scope_run AND r.pipeline_contract_version='full-strict-v20' AND r.pipeline_resume_state='RESUMABLE' AND s.status='COMPLETED' AND (SELECT count(*) FROM public.run_step_outputs so JOIN public.run_output_objects ro ON ro.tenant_id=so.tenant_id AND ro.deal_id=so.deal_id AND ro.run_id=so.run_id AND ro.output_object_id=so.output_object_id WHERE so.tenant_id=s.tenant_id AND so.run_id=s.run_id AND so.step_id=s.step_id AND so.subrole='__step__' AND ro.output_class='STEP_RECEIPT')<>1) OR EXISTS (SELECT 1 FROM public.run_output_objects ro WHERE ro.tenant_id=scope_tenant AND ro.deal_id=scope_deal AND ro.run_id=scope_run AND (SELECT count(*) FROM public.run_step_outputs so WHERE so.tenant_id=ro.tenant_id AND so.deal_id=ro.deal_id AND so.run_id=ro.run_id AND so.output_object_id=ro.output_object_id)<>1) OR EXISTS (SELECT 1 FROM public.run_expected_consumer_edges e JOIN public.run_steps s ON s.tenant_id=e.tenant_id AND s.run_id=e.run_id AND s.step_name=e.consumer_step_name WHERE e.tenant_id=scope_tenant AND e.deal_id=scope_deal AND e.run_id=scope_run AND s.status='COMPLETED' AND (SELECT count(*) FROM public.run_consumer_receipts c WHERE c.tenant_id=e.tenant_id AND c.deal_id=e.deal_id AND c.run_id=e.run_id AND c.expected_edge_id=e.expected_edge_id)<>1) OR EXISTS (SELECT 1 FROM public.run_consumer_receipts c JOIN public.run_expected_consumer_edges e ON e.tenant_id=c.tenant_id AND e.deal_id=c.deal_id AND e.run_id=c.run_id AND e.expected_edge_id=c.expected_edge_id WHERE c.tenant_id=scope_tenant AND c.deal_id=scope_deal AND c.run_id=scope_run AND (c.consumer_step_name,c.consumer_subrole) IS DISTINCT FROM (e.consumer_step_name,e.consumer_subrole)) OR EXISTS (SELECT 1 FROM public.run_consumer_receipts c JOIN public.run_output_objects consumer_root ON consumer_root.tenant_id=c.tenant_id AND consumer_root.deal_id=c.deal_id AND consumer_root.run_id=c.run_id AND consumer_root.output_object_id=c.consumer_output_object_id WHERE c.tenant_id=scope_tenant AND c.deal_id=scope_deal AND c.run_id=scope_run AND consumer_root.output_class<>'STEP_RECEIPT') OR EXISTS (WITH expected(step_name,subrole,output_kind,output_ordinal,semantic_rule_version,semantic_rule_hash) AS (VALUES ('DATA_ROOM_INVENTORY_PACKAGE','__step__','STATUS_ONLY_INVENTORY_V1',0,'2','7fb7c7d9f4af3bc9c0f224aa9a868dc20c3f77cc22c35f0d2f5f4b4bf3e9ef06'),('DATA_ROOM_INGESTION_HANDOFF','__step__','STATUS_ONLY_HANDOFF_V1',0,'2','0e61d997dc89050ebaf2d72b3459bc41a64cde650893f3b36414fa371105fa1a'),('INGEST_CHECK','__step__','STATUS_ONLY_INGEST_CHECK_V1',0,'2','6634007f7e2d2ac83631d9bb386450de73bd30ca1e8ee5529de10cba9994df03'),('DOCUMENT_PREFLIGHT','__step__','STATUS_ONLY_PREFLIGHT_V1',0,'2','af64299ed8f2cdac1c9cf2e1fa7ce8f585df6d8dbe9dfd8980dcdc238b16e7a7'),('METHODOLOGY_COVERAGE_INIT','__step__','METHODOLOGY_COVERAGE_RECEIPT_V1',0,'2','5b157c8329a0a1c229f6ffb3ec2cfc0c0be1c6fb85f0dc0a669883aa1eb75dbc'),('METHODOLOGY_EXTRACTION_TASK_PLANNING','__step__','METHODOLOGY_TASK_PLAN_RECEIPT_V1',0,'2','07e0980ebfa04d3aae5042ec39b07200cacc87cc5f8ad40db6338faa56bb38af'),('METHODOLOGY_EXTRACTION_TASK_EXECUTION','__step__','METHODOLOGY_EXECUTION_RECEIPT_V1',0,'2','0550f5d7335852771e830ae9f376a9633813a24fd7afc677c32c45ee1446dded'),('METHODOLOGY_CLAIM_MATERIALIZATION','__step__','METHODOLOGY_CLAIM_RECEIPT_V1',0,'2','63ae11aa758eea76bfa4931cc35ea6bd2b42a827d58b7f058f000db2e439792c'),('METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION','__step__','METHODOLOGY_EVIDENCE_RECEIPT_V1',0,'2','7ca82a99ce4bf6782d76b17fdc8434bf98fc92e1c4626bcf2121004bed63f94e'),('METHODOLOGY_SANAD_CREATION_LINKING_GRADING','__step__','METHODOLOGY_SANAD_RECEIPT_V1',0,'2','ca4f4b8c77d274999f9593829ff76cbe0839e05f82082a37d8554cc1bff2dd52'),('METHODOLOGY_DETERMINISTIC_CALCULATION','__step__','METHODOLOGY_CALC_RECEIPT_V1',0,'2','2db75b6695f26cfbf7f09ef0a440da557c15eb9b960be86d78f62f70412617ed'),('METHODOLOGY_TRUTH_DASHBOARD','__step__','METHODOLOGY_DASHBOARD_RECEIPT_V1',0,'2','29a48e733fe07251ed0bb509311ddfbb36936876913c84ce149e79cd5336eea9'),('EXTRACT','__step__','EXTRACT_RECEIPT_V1',0,'2','ba078b98792baa4f5656ebdd21659a02f659a8e666a6f53b2e094c6c666c065f'),('EXTRACT','rag_context','EXTRACT_RAG_CONTEXT_RECEIPT_V1',0,'2','ba078b98792baa4f5656ebdd21659a02f659a8e666a6f53b2e094c6c666c065f'),('GRADE','__step__','GRADE_RECEIPT_V1',0,'2','c24aa9f98b6a0ff2d405cfe916bdcf3fe3082e20685d671bfbf5b62853060e35'),('CALC','__step__','CALC_RECEIPT_V1',0,'2','8158acc63c864ef44cfc4ba7e622268e603ccad36f77729f3e09483ecf3e17ef'),('METHODOLOGY_COMPANY_IDENTITY_PACKAGE','__step__','COMPANY_IDENTITY_RECEIPT_V1',0,'2','3c591896fcae85db3d541beed32442a3824dcbd7df7568794e35181cb61c030e'),('ENRICHMENT','__step__','ENRICHMENT_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','sec_edgar','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','companies_house','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','github','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','fred','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','finnhub','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','fmp','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','world_bank','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','escwa_catalog','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','escwa_ispar','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','qatar_open_data','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','hackernews','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','gdelt','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','patentsview','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','wayback','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','google_news_rss','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','epo_open_patent','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','semantic_scholar','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('ENRICHMENT','perplexity','ENRICHMENT_PROVIDER_OUTCOME_RECEIPT_V1',0,'2','0788fb0c4878890c1214d71f1950daabd9a529cbb651fab332d8870d6a3230a6'),('GRAPH_EVIDENCE','__step__','GRAPH_RECEIPT_V1',0,'2','11d0a5a8557433a21994ac042a5d248ec98b1310c4374df66d3ebb1705262298'),('GRAPH_EVIDENCE','internal_evidence','GRAPH_NAMESPACE_RECEIPT_V1',0,'2','11d0a5a8557433a21994ac042a5d248ec98b1310c4374df66d3ebb1705262298'),('GRAPH_EVIDENCE','external_context','GRAPH_NAMESPACE_RECEIPT_V1',0,'2','11d0a5a8557433a21994ac042a5d248ec98b1310c4374df66d3ebb1705262298'),('RAG_EVIDENCE','__step__','RAG_RECEIPT_V1',0,'2','de1aa5c89992b1eec574448a6f857d419782126544b556ae177f8fa8356508f9'),('RAG_EVIDENCE','internal_evidence','RAG_NAMESPACE_RECEIPT_V1',0,'2','de1aa5c89992b1eec574448a6f857d419782126544b556ae177f8fa8356508f9'),('RAG_EVIDENCE','external_context','RAG_NAMESPACE_RECEIPT_V1',0,'2','de1aa5c89992b1eec574448a6f857d419782126544b556ae177f8fa8356508f9'),('METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN','__step__','CONFLICT_CHECK_RECEIPT_V1',0,'2','5b3564d3cc1874e209477d733527dba5a4cbaf59b5ef4f4b308b5ca902f2f3c8'),('METHODOLOGY_EVIDENCE_TRUST_COURT','__step__','EVIDENCE_TRUST_COURT_RECEIPT_V1',0,'2','7cc67598d4fc5e6e49e3bd927deca66227b7be2f9a2a80bc1bd735bb243e5cbf'),('METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE','__step__','VEP_RECEIPT_V1',0,'2','29883670e0e8512905cb08e979f77b2b5cc158667f7db082518b937272fb4b94'),('DEBATE','__step__','DEBATE_RECEIPT_V1',0,'2','ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83'),('DEBATE','advocate','DEBATE_ROLE_RECEIPT_V1',0,'2','ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83'),('DEBATE','sanad_breaker','DEBATE_ROLE_RECEIPT_V1',0,'2','ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83'),('DEBATE','contradiction_finder','DEBATE_ROLE_RECEIPT_V1',0,'2','ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83'),('DEBATE','risk_officer','DEBATE_ROLE_RECEIPT_V1',0,'2','ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83'),('DEBATE','arbiter','DEBATE_ROLE_RECEIPT_V1',0,'2','ab67b37ab0677d66c7896c6f68165ce7bb41e11ad0204ee34bfefa48c116da83'),('ANALYSIS','__step__','ANALYSIS_RECEIPT_V1',0,'2','aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa'),('ANALYSIS','financial_agent','ANALYSIS_AGENT_RECEIPT_V1',0,'2','aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa'),('ANALYSIS','market_agent','ANALYSIS_AGENT_RECEIPT_V1',0,'2','aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa'),('ANALYSIS','technical_agent','ANALYSIS_AGENT_RECEIPT_V1',0,'2','aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa'),('ANALYSIS','terms_agent','ANALYSIS_AGENT_RECEIPT_V1',0,'2','aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa'),('ANALYSIS','team_agent','ANALYSIS_AGENT_RECEIPT_V1',0,'2','aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa'),('ANALYSIS','risk_officer_agent','ANALYSIS_AGENT_RECEIPT_V1',0,'2','aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa'),('ANALYSIS','historian_agent','ANALYSIS_AGENT_RECEIPT_V1',0,'2','aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa'),('ANALYSIS','sector_specialist_agent','ANALYSIS_AGENT_RECEIPT_V1',0,'2','aec36ddf4840de4201a0eefe2c39736257ee566cdd366ba76650f4187b8653aa'),('SCORING','__step__','SCORING_RECEIPT_V1',0,'2','0c9c74af82ccd30eb683a40aedbef32c1877618a415d3e330a08039029045177'),('METHODOLOGY_LAYER2_READINESS_PACKAGE','__step__','LAYER2_READINESS_RECEIPT_V1',0,'2','49930fbde3c063c9b02a3501506fa6364d482857635745be255ce186206dd69c'),('LAYER2_IC_CHALLENGE','__step__','LAYER2_RECEIPT_V1',0,'2','3f144df6c9855f7733689553d24af2c15065d7004e4b1adccd0ca9f9500b366e'),('LAYER2_IC_CHALLENGE','ic_advocate','LAYER2_ROLE_RECEIPT_V1',0,'2','3f144df6c9855f7733689553d24af2c15065d7004e4b1adccd0ca9f9500b366e'),('LAYER2_IC_CHALLENGE','ic_challenger','LAYER2_ROLE_RECEIPT_V1',0,'2','3f144df6c9855f7733689553d24af2c15065d7004e4b1adccd0ca9f9500b366e'),('LAYER2_IC_CHALLENGE','ic_arbiter','LAYER2_ROLE_RECEIPT_V1',0,'2','3f144df6c9855f7733689553d24af2c15065d7004e4b1adccd0ca9f9500b366e'),('DELIVERABLES','__step__','PRODUCT_BUNDLE_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','reconciliation','DELIVERABLE_RECONCILIATION_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','screening_snapshot_pdf','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','screening_snapshot_docx','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','ic_memo_pdf','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','ic_memo_docx','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','truth_dashboard','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','qa_brief','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','executive_summary','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','commercial_diligence','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','financial_diligence','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','risk_register','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','layer2_ic_challenge','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','evidence_index','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','run_summary','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','provenance_appendix','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d'),('DELIVERABLES','product_bundle_manifest','DELIVERABLE_ARTIFACT_RECEIPT_V1',0,'2','672313277a363a45f7b477db7b2715b00d68c2bc7f5b2cc43cdca8556f9f161d')), required AS (SELECT s.tenant_id,r.deal_id,s.run_id,s.step_id,expected.step_name,expected.subrole,expected.output_kind,expected.output_ordinal,expected.semantic_rule_version,expected.semantic_rule_hash FROM public.run_steps s JOIN public.runs r ON r.tenant_id=s.tenant_id AND r.run_id=s.run_id JOIN expected ON expected.step_name=s.step_name WHERE s.tenant_id=scope_tenant AND s.run_id=scope_run AND r.deal_id=scope_deal AND r.pipeline_contract_version='full-strict-v20' AND r.pipeline_resume_state='RESUMABLE' AND s.status='COMPLETED'), actual AS (SELECT so.tenant_id,so.deal_id,so.run_id,so.step_id,so.step_name,so.subrole,so.output_kind,so.output_ordinal,so.semantic_rule_version,so.semantic_rule_hash FROM public.run_step_outputs so JOIN public.runs actual_run ON actual_run.tenant_id=so.tenant_id AND actual_run.deal_id=so.deal_id AND actual_run.run_id=so.run_id AND actual_run.pipeline_contract_version='full-strict-v20' AND actual_run.pipeline_resume_state='RESUMABLE' JOIN public.run_output_objects ro ON ro.tenant_id=so.tenant_id AND ro.deal_id=so.deal_id AND ro.run_id=so.run_id AND ro.output_object_id=so.output_object_id WHERE so.tenant_id=scope_tenant AND so.deal_id=scope_deal AND so.run_id=scope_run AND ro.output_class='STEP_RECEIPT' AND ro.output_kind=so.output_kind) SELECT 1 FROM required FULL JOIN actual ON actual.tenant_id=required.tenant_id AND actual.deal_id=required.deal_id AND actual.run_id=required.run_id AND actual.step_id=required.step_id AND actual.step_name=required.step_name AND actual.subrole=required.subrole AND actual.output_kind=required.output_kind AND actual.output_ordinal=required.output_ordinal AND actual.semantic_rule_version=required.semantic_rule_version AND actual.semantic_rule_hash=required.semantic_rule_hash WHERE required.step_id IS NULL OR actual.step_id IS NULL) OR EXISTS (SELECT 1 FROM public.deliverable_manifest_references m WHERE m.tenant_id=scope_tenant AND m.deal_id=scope_deal AND m.run_id=scope_run GROUP BY m.manifest_deliverable_id,m.manifest_output_object_id HAVING count(*)<>15 OR count(*) FILTER (WHERE member_kind='ARTIFACT')<>14 OR count(*) FILTER (WHERE member_kind='RECONCILIATION')<>1) OR EXISTS (SELECT 1 FROM public.deliverable_manifest_references m JOIN public.runs r ON r.tenant_id=m.tenant_id AND r.deal_id=m.deal_id AND r.run_id=m.run_id JOIN public.run_output_objects member_root ON member_root.tenant_id=m.tenant_id AND member_root.deal_id=m.deal_id AND member_root.run_id=m.run_id AND member_root.output_object_id=m.member_output_object_id LEFT JOIN public.deliverables member_deliverable ON member_deliverable.tenant_id=m.tenant_id AND member_deliverable.deal_id=m.deal_id AND member_deliverable.run_id=m.run_id AND member_deliverable.run_output_object_id=m.member_output_object_id WHERE m.tenant_id=scope_tenant AND m.deal_id=scope_deal AND m.run_id=scope_run AND (m.member_identity_sha256 IS DISTINCT FROM member_root.native_identity_sha256 OR m.member_value_sha256 IS DISTINCT FROM member_root.native_output_sha256 OR (m.member_kind='ARTIFACT' AND (member_deliverable.deliverable_id IS NULL OR m.catalog_ordinal IS DISTINCT FROM member_deliverable.catalog_ordinal OR m.member_content_sha256 IS DISTINCT FROM member_deliverable.content_sha256)) OR (m.member_kind='RECONCILIATION' AND m.member_content_sha256 IS NOT NULL) OR m.reference_sha256 IS DISTINCT FROM public.idis_task7_manifest_reference_digest_v1(m.tenant_id,m.deal_id,m.run_id,r.pipeline_contract_version,r.pipeline_contract_hash,m.manifest_deliverable_id,m.member_kind,m.catalog_ordinal,m.member_step_output_id,m.member_output_object_id,m.member_identity_sha256,m.member_value_sha256,m.member_content_sha256))) THEN RAISE EXCEPTION 'CONSUMER_RECEIPT_INCOMPLETE' USING ERRCODE='23514'; END IF; RETURN NULL; END$$",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_run_steps_receipt_complete AFTER INSERT OR UPDATE OR DELETE ON public.run_steps DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_receipt_equality_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_run_step_outputs_receipt_complete AFTER INSERT OR UPDATE OR DELETE ON public.run_step_outputs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_receipt_equality_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_run_output_objects_receipt_complete AFTER INSERT OR UPDATE OR DELETE ON public.run_output_objects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_receipt_equality_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_expected_edges_decision_complete AFTER INSERT OR UPDATE OR DELETE ON public.run_expected_consumer_edges DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_receipt_equality_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_consumer_receipts_edge_complete AFTER INSERT OR UPDATE OR DELETE ON public.run_consumer_receipts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_receipt_equality_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_deliverable_manifest_references_exact_one AFTER INSERT OR UPDATE OR DELETE ON public.deliverable_manifest_references DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_receipt_equality_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_deliverable_manifest_references_exact_projection AFTER INSERT OR UPDATE OR DELETE ON public.deliverable_manifest_references DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_receipt_equality_v1()",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_receipt_equality_v1() FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_current_input_binding_admission_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$DECLARE preimage text; expected_hash text; BEGIN IF TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION 'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; preimage:='[\"run-input-binding-digest-v2\",'||pg_catalog.to_json(normalize(NEW.tenant_id::text,NFC))::text||','||pg_catalog.to_json(normalize(NEW.deal_id::text,NFC))::text||','||pg_catalog.to_json(normalize(NEW.run_id::text,NFC))::text||','||pg_catalog.to_json(normalize(NEW.pipeline_contract_version,NFC))::text||','||pg_catalog.to_json(normalize(NEW.pipeline_contract_hash,NFC))::text||','||pg_catalog.to_json(normalize(NEW.corpus_receipt_id::text,NFC))::text||','||pg_catalog.to_json(normalize(NEW.source_lineage_object_id::text,NFC))::text||','||pg_catalog.to_json(normalize(NEW.source_object_kind,NFC))::text||','||pg_catalog.to_json(normalize(NEW.source_disposition,NFC))::text||','||pg_catalog.to_json(normalize(NEW.binding_purpose,NFC))::text||','||pg_catalog.to_json(normalize(NEW.selected_source_version,NFC))::text||','||pg_catalog.to_json(normalize(NEW.selected_identity_sha256,NFC))::text||','||pg_catalog.to_json(normalize(NEW.selected_content_sha256,NFC))::text||','||pg_catalog.to_json(normalize(NEW.corpus_receipt_sha256,NFC))::text||']'; expected_hash:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(preimage,'UTF8')),'hex'); IF expected_hash<>NEW.binding_sha256 THEN RAISE EXCEPTION 'PIPELINE_CONTRACT_HASH_MISMATCH' USING ERRCODE='23514'; END IF; IF NOT EXISTS (SELECT 1 FROM public.runs r JOIN public.strict_preflight_proofs p ON p.tenant_id=r.tenant_id AND p.deal_id=r.deal_id AND p.proof_id=r.strict_preflight_proof_id AND p.consumed_by_run_id=r.run_id JOIN public.data_room_packages pkg ON pkg.tenant_id=r.tenant_id AND pkg.deal_id=r.deal_id AND pkg.package_id=NEW.corpus_receipt_id JOIN public.source_lineage_objects package_root ON package_root.tenant_id=pkg.tenant_id AND package_root.deal_id=pkg.deal_id AND package_root.source_lineage_object_id=pkg.source_lineage_object_id AND package_root.object_kind='PACKAGE' JOIN public.source_lineage_objects selected_root ON selected_root.tenant_id=NEW.tenant_id AND selected_root.deal_id=NEW.deal_id AND selected_root.source_lineage_object_id=NEW.source_lineage_object_id AND selected_root.object_kind=NEW.source_object_kind WHERE r.tenant_id=NEW.tenant_id AND r.deal_id=NEW.deal_id AND r.run_id=NEW.run_id AND r.status='QUEUED' AND pkg.status='SEALED' AND pkg.source_binding_state='BOUND' AND package_root.corpus_receipt_id=NEW.corpus_receipt_id AND package_root.corpus_receipt_sha256=NEW.corpus_receipt_sha256 AND package_root.content_sha256=NEW.corpus_receipt_sha256 AND selected_root.corpus_receipt_id=NEW.corpus_receipt_id AND selected_root.corpus_receipt_sha256=NEW.corpus_receipt_sha256 AND p.corpus_hash=NEW.corpus_receipt_sha256 AND p.pipeline_contract_version=NEW.pipeline_contract_version AND p.pipeline_contract_hash=NEW.pipeline_contract_hash AND r.pipeline_contract_version=NEW.pipeline_contract_version AND r.pipeline_contract_hash=NEW.pipeline_contract_hash) THEN RAISE EXCEPTION 'STRICT_PREFLIGHT_PROOF_MISMATCH' USING ERRCODE='23514'; END IF; RETURN NEW; END$$",
    "CREATE TRIGGER trg_task7_run_input_bindings_append_only BEFORE INSERT OR UPDATE OR DELETE ON public.run_input_bindings FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_input_binding_admission_v1()",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_input_binding_admission_v1() FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_expected_edge_admission_is_valid_v1(p_row jsonb,p_tenant uuid,p_deal uuid,p_run uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$SELECT p_row->>'materialization_origin'='ADMISSION' AND p_row->>'contract_rule_id' IN ('E001_SOURCE_INVENTORY_ADMISSION','E002_SOURCE_HANDOFF_ADMISSION') AND p_row->>'usage_kind' IN ('SOURCE_INVENTORY','SOURCE_HANDOFF') AND p_row->>'producer_input_binding_id' IS NOT NULL AND p_row->>'producer_output_object_id' IS NULL AND p_row->>'lease_owner_id' IS NULL AND p_row->>'lease_fencing_token'='0' AND EXISTS (SELECT 1 FROM public.runs r WHERE r.tenant_id=p_tenant AND r.deal_id=p_deal AND r.run_id=p_run AND r.status='QUEUED')$$; REVOKE ALL ON FUNCTION public.idis_task7_expected_edge_admission_is_valid_v1(jsonb,uuid,uuid,uuid) FROM PUBLIC; CREATE FUNCTION public.idis_task7_current_append_only_lease_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$DECLARE scope_tenant uuid; scope_deal uuid; scope_run uuid; owner_id uuid; fencing_token bigint; BEGIN IF TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION 'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; scope_tenant:=NEW.tenant_id; scope_deal:=NEW.deal_id; scope_run:=NEW.run_id; IF TG_TABLE_NAME='run_expected_consumer_edges' AND public.idis_task7_expected_edge_admission_is_valid_v1(pg_catalog.to_jsonb(NEW),scope_tenant,scope_deal,scope_run) THEN RETURN NEW; END IF; BEGIN owner_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_owner_id',true),'')::uuid; fencing_token:=NULLIF(pg_catalog.current_setting('idis.worker_lease_fencing_token',true),'')::bigint; EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END; IF NEW.lease_owner_id IS DISTINCT FROM owner_id OR NEW.lease_fencing_token IS DISTINCT FROM fencing_token OR NOT EXISTS (SELECT 1 FROM public.runs r WHERE r.tenant_id=scope_tenant AND r.deal_id=scope_deal AND r.run_id=scope_run AND r.status='RUNNING' AND r.lease_owner_id=owner_id AND r.lease_fencing_token=fencing_token AND r.lease_expires_at>pg_catalog.statement_timestamp()) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; RETURN NEW; END$$",
    "CREATE TRIGGER trg_task7_run_output_objects_append_only BEFORE INSERT OR UPDATE OR DELETE ON public.run_output_objects FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_append_only_lease_v1()",
    "CREATE TRIGGER trg_task7_run_step_outputs_append_only BEFORE INSERT OR UPDATE OR DELETE ON public.run_step_outputs FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_append_only_lease_v1()",
    "CREATE TRIGGER trg_task7_run_applicability_decisions_append_only BEFORE INSERT OR UPDATE OR DELETE ON public.run_applicability_decisions FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_append_only_lease_v1()",
    "CREATE TRIGGER trg_task7_run_expected_consumer_edges_append_only BEFORE INSERT OR UPDATE OR DELETE ON public.run_expected_consumer_edges FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_append_only_lease_v1()",
    "CREATE TRIGGER trg_task7_run_consumer_receipts_append_only BEFORE INSERT OR UPDATE OR DELETE ON public.run_consumer_receipts FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_append_only_lease_v1()",
    "CREATE TRIGGER trg_task7_deliverable_manifest_references_append_only BEFORE INSERT OR UPDATE OR DELETE ON public.deliverable_manifest_references FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_append_only_lease_v1()",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_append_only_lease_v1() FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_package_source_transition_is_valid_v1(p_old jsonb,p_new jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$SELECT p_old->>'status'='OPEN' AND p_new->>'status'='SEALED' AND (p_new-ARRAY['status','source_binding_state','source_lineage_object_id','source_corpus_receipt_id','source_lineage_object_kind','updated_at']::text[]) IS NOT DISTINCT FROM (p_old-ARRAY['status','source_binding_state','source_lineage_object_id','source_corpus_receipt_id','source_lineage_object_kind','updated_at']::text[])$$; REVOKE ALL ON FUNCTION public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb) FROM PUBLIC; CREATE FUNCTION public.idis_task7_current_source_bound_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$BEGIN IF TG_OP='INSERT' THEN IF TG_TABLE_NAME<>'data_room_package_files' OR NEW.source_binding_state<>'UNBOUND' OR NEW.source_lineage_object_id IS NOT NULL THEN RAISE EXCEPTION 'SOURCE_TREE_MUTATED' USING ERRCODE='23514'; END IF; PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('task7-source-package-v1:'||NEW.package_id::text,0)); IF NOT EXISTS (SELECT 1 FROM public.data_room_packages p WHERE p.tenant_id=NEW.tenant_id AND p.deal_id=NEW.deal_id AND p.package_id=NEW.package_id) OR EXISTS (SELECT 1 FROM public.data_room_packages p WHERE p.tenant_id=NEW.tenant_id AND p.deal_id=NEW.deal_id AND p.package_id=NEW.package_id AND (p.status='SEALED' OR p.source_binding_state='BOUND')) THEN RAISE EXCEPTION 'SOURCE_TREE_MUTATED' USING ERRCODE='23514'; END IF; RETURN NEW; END IF; IF TG_TABLE_NAME='data_room_packages' THEN PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('task7-source-package-v1:'||OLD.package_id::text,0)); END IF; IF TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION 'TASK7_SOURCE_FREEZE_FENCED' USING ERRCODE='40001'; END IF; IF OLD.source_binding_state='BOUND' OR NEW.source_binding_state NOT IN ('UNBOUND','BOUND') OR (OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' AND (NEW.source_lineage_object_id IS NULL OR (TG_TABLE_NAME NOT IN ('data_room_packages','data_room_package_files') AND NEW.source_corpus_receipt_id IS NULL))) OR (OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' AND TG_TABLE_NAME='data_room_packages' AND NOT public.idis_task7_package_source_transition_is_valid_v1(pg_catalog.to_jsonb(OLD),pg_catalog.to_jsonb(NEW))) OR (OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' AND TG_TABLE_NAME='document_spans' AND (pg_catalog.to_jsonb(NEW)-ARRAY['source_binding_state','source_lineage_object_id','source_corpus_receipt_id','source_lineage_object_kind','parse_attempt_id']::text[]) IS DISTINCT FROM (pg_catalog.to_jsonb(OLD)-ARRAY['source_binding_state','source_lineage_object_id','source_corpus_receipt_id','source_lineage_object_kind','parse_attempt_id']::text[])) OR (OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' AND TG_TABLE_NAME='data_room_package_files' AND (pg_catalog.to_jsonb(NEW)-ARRAY['source_binding_state','source_lineage_object_id','source_corpus_receipt_id','source_lineage_object_kind','doc_id','document_id']::text[]) IS DISTINCT FROM (pg_catalog.to_jsonb(OLD)-ARRAY['source_binding_state','source_lineage_object_id','source_corpus_receipt_id','source_lineage_object_kind','doc_id','document_id']::text[])) OR (OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' AND TG_TABLE_NAME NOT IN ('data_room_packages','document_spans','data_room_package_files') AND (pg_catalog.to_jsonb(NEW)-ARRAY['source_binding_state','source_lineage_object_id','source_corpus_receipt_id','source_lineage_object_kind']::text[]) IS DISTINCT FROM (pg_catalog.to_jsonb(OLD)-ARRAY['source_binding_state','source_lineage_object_id','source_corpus_receipt_id','source_lineage_object_kind']::text[])) THEN RAISE EXCEPTION 'TASK7_SOURCE_FREEZE_FENCED' USING ERRCODE='40001'; END IF; RETURN NEW; END$$",
    "CREATE TRIGGER trg_task7_data_room_packages_source_bound BEFORE UPDATE OR DELETE ON public.data_room_packages FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_bound_v1()",
    "CREATE TRIGGER trg_task7_data_room_package_files_source_bound BEFORE INSERT OR UPDATE OR DELETE ON public.data_room_package_files FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_bound_v1()",
    "CREATE TRIGGER trg_task7_document_artifacts_source_bound BEFORE UPDATE OR DELETE ON public.document_artifacts FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_bound_v1()",
    "CREATE TRIGGER trg_task7_documents_source_bound BEFORE UPDATE OR DELETE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_bound_v1()",
    "CREATE TRIGGER trg_task7_document_parse_attempts_source_bound BEFORE UPDATE OR DELETE ON public.document_parse_attempts FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_bound_v1()",
    "CREATE TRIGGER trg_task7_document_spans_source_bound BEFORE UPDATE OR DELETE ON public.document_spans FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_source_bound_v1()",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_source_bound_v1() FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_current_output_bound_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$BEGIN IF TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION 'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; IF OLD.run_output_object_id IS NOT NULL AND pg_catalog.to_jsonb(NEW) IS DISTINCT FROM pg_catalog.to_jsonb(OLD) THEN RAISE EXCEPTION 'STEP_REHYDRATION_FAILED' USING ERRCODE='23514'; END IF; RETURN NEW; END$$",
    "CREATE TRIGGER trg_task7_methodology_coverage_records_output_bound BEFORE UPDATE OR DELETE ON public.methodology_coverage_records FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_methodology_extraction_tasks_output_bound BEFORE UPDATE OR DELETE ON public.methodology_extraction_tasks FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_methodology_extraction_results_output_bound BEFORE UPDATE OR DELETE ON public.methodology_extraction_results FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_methodology_result_factual_outputs_output_bound BEFORE UPDATE OR DELETE ON public.methodology_result_factual_outputs FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_methodology_result_claim_links_output_bound BEFORE UPDATE OR DELETE ON public.methodology_result_claim_links FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_methodology_result_evidence_links_output_bound BEFORE UPDATE OR DELETE ON public.methodology_result_evidence_links FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_methodology_result_sanad_links_output_bound BEFORE UPDATE OR DELETE ON public.methodology_result_sanad_links FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_methodology_result_defect_links_output_bound BEFORE UPDATE OR DELETE ON public.methodology_result_defect_links FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_deterministic_calculations_output_bound BEFORE UPDATE OR DELETE ON public.deterministic_calculations FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_vector_embeddings_output_bound BEFORE UPDATE OR DELETE ON public.vector_embeddings FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_evidence_trust_findings_output_bound BEFORE UPDATE OR DELETE ON public.evidence_trust_findings FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_muhasabah_records_output_bound BEFORE UPDATE OR DELETE ON public.muhasabah_records FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_validated_evidence_packages_output_bound BEFORE UPDATE OR DELETE ON public.validated_evidence_packages FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_debate_sessions_output_bound BEFORE UPDATE OR DELETE ON public.debate_sessions FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_layer2_ic_challenges_output_bound BEFORE UPDATE OR DELETE ON public.layer2_ic_challenges FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_layer2_ic_findings_output_bound BEFORE UPDATE OR DELETE ON public.layer2_ic_findings FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "CREATE TRIGGER trg_task7_deliverables_output_bound BEFORE UPDATE OR DELETE ON public.deliverables FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_output_bound_v1()",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_output_bound_v1() FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_current_runs_contract_immutable_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$BEGIN IF (NEW.pipeline_contract_version,NEW.pipeline_contract_hash,NEW.pipeline_resume_state,NEW.pipeline_resume_error_code) IS DISTINCT FROM (OLD.pipeline_contract_version,OLD.pipeline_contract_hash,OLD.pipeline_resume_state,OLD.pipeline_resume_error_code) THEN RAISE EXCEPTION 'PIPELINE_CONTRACT_HASH_MISMATCH' USING ERRCODE='23514'; END IF; RETURN NEW; END$$",
    "CREATE TRIGGER trg_task7_runs_contract_immutable BEFORE UPDATE ON public.runs FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_runs_contract_immutable_v1()",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_runs_contract_immutable_v1() FROM PUBLIC",
    "CREATE FUNCTION public.idis_task7_current_run_steps_contract_immutable_v1() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$BEGIN IF (NEW.step_id,NEW.tenant_id,NEW.run_id,NEW.step_name,NEW.step_order,NEW.pipeline_contract_version,NEW.pipeline_contract_hash,NEW.semantic_rule_version,NEW.semantic_rule_hash) IS DISTINCT FROM (OLD.step_id,OLD.tenant_id,OLD.run_id,OLD.step_name,OLD.step_order,OLD.pipeline_contract_version,OLD.pipeline_contract_hash,OLD.semantic_rule_version,OLD.semantic_rule_hash) THEN RAISE EXCEPTION 'PIPELINE_CONTRACT_HASH_MISMATCH' USING ERRCODE='23514'; END IF; RETURN NEW; END$$",
    "CREATE TRIGGER trg_task7_run_steps_contract_immutable BEFORE UPDATE ON public.run_steps FOR EACH ROW EXECUTE FUNCTION public.idis_task7_current_run_steps_contract_immutable_v1()",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_run_steps_contract_immutable_v1() FROM PUBLIC",
    "CREATE OR REPLACE FUNCTION public.idis_task5_coverage_scope_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$BEGIN IF TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING ERRCODE='23514'; END IF; IF TG_OP='UPDATE' AND (NEW.coverage_record_id IS DISTINCT FROM OLD.coverage_record_id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.deal_id IS DISTINCT FROM OLD.deal_id OR NEW.run_id IS DISTINCT FROM OLD.run_id OR NEW.selection_id IS DISTINCT FROM OLD.selection_id OR NEW.methodology_version_record_id IS DISTINCT FROM OLD.methodology_version_record_id OR NEW.methodology_version_id IS DISTINCT FROM OLD.methodology_version_id OR NEW.methodology_question_record_id IS DISTINCT FROM OLD.methodology_question_record_id OR NEW.methodology_question_id IS DISTINCT FROM OLD.methodology_question_id OR NEW.methodology_type IS DISTINCT FROM OLD.methodology_type OR NEW.run_output_object_id IS DISTINCT FROM OLD.run_output_object_id OR NEW.created_at IS DISTINCT FROM OLD.created_at) THEN RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING ERRCODE='23514'; END IF; IF TG_OP='INSERT' AND NEW.run_output_object_id IS NULL AND EXISTS (SELECT 1 FROM public.runs r WHERE r.tenant_id=NEW.tenant_id AND r.deal_id=NEW.deal_id AND r.run_id=NEW.run_id AND r.pipeline_contract_version='full-strict-v20') THEN RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_INVALID' USING ERRCODE='23514'; END IF; IF NOT EXISTS (SELECT 1 FROM public.run_methodology_selections s JOIN public.methodology_questions q ON q.tenant_id=NEW.tenant_id AND q.methodology_version_record_id=NEW.methodology_version_record_id AND q.methodology_version_id=NEW.methodology_version_id AND q.methodology_question_record_id=NEW.methodology_question_record_id AND q.methodology_question_id=NEW.methodology_question_id AND q.methodology_type=NEW.methodology_type WHERE s.tenant_id=NEW.tenant_id AND s.deal_id=NEW.deal_id AND s.run_id=NEW.run_id AND s.selection_id=NEW.selection_id AND ((q.methodology_type='commercial_dd' AND q.methodology_version_record_id=s.commercial_dd_version_record_id) OR (q.methodology_type='financial_dd' AND q.methodology_version_record_id=s.financial_dd_version_record_id))) THEN RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_INVALID' USING ERRCODE='23514'; END IF; NEW.updated_at:=pg_catalog.now(); RETURN NEW; END$$",
    "CREATE FUNCTION public.idis_task6_output_root_attach_is_valid_v1(p_old jsonb,p_new jsonb,p_tenant uuid,p_deal uuid,p_run uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$SELECT p_old->>'run_output_object_id' IS NULL AND p_new->>'run_output_object_id' IS NOT NULL AND (p_new-ARRAY['run_output_object_id']::text[]) IS NOT DISTINCT FROM (p_old-ARRAY['run_output_object_id']::text[]) AND public.idis_task6_current_lease_is_valid(p_tenant,p_deal,p_run) AND EXISTS (SELECT 1 FROM public.run_steps s WHERE s.tenant_id=p_tenant AND s.run_id=p_run AND s.step_name='METHODOLOGY_EXTRACTION_TASK_EXECUTION' AND s.step_order=6 AND s.status='RUNNING') AND EXISTS (SELECT 1 FROM public.run_output_objects r WHERE r.tenant_id=p_tenant AND r.deal_id=p_deal AND r.run_id=p_run AND r.output_object_id=(p_new->>'run_output_object_id')::uuid AND r.output_class=p_new->>'run_output_object_class' AND r.output_kind=p_new->>'run_output_object_kind')$$; REVOKE ALL ON FUNCTION public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid) FROM PUBLIC; CREATE OR REPLACE FUNCTION public.idis_task6_immutable_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$BEGIN IF TG_OP='INSERT' THEN IF NOT public.idis_task6_current_lease_is_valid(NEW.tenant_id,NEW.deal_id,NEW.run_id) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; RETURN NEW; END IF; IF TG_OP='DELETE' THEN IF public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE' USING ERRCODE='23514'; END IF; IF TG_OP='UPDATE' AND TG_TABLE_NAME IN ('methodology_extraction_results','methodology_result_factual_outputs') AND public.idis_task6_output_root_attach_is_valid_v1(pg_catalog.to_jsonb(OLD),(pg_catalog.to_jsonb(NEW)-ARRAY['run_output_object_class','run_output_object_kind']::text[])||pg_catalog.jsonb_build_object('run_output_object_class','NATIVE_ROW','run_output_object_kind',CASE TG_TABLE_NAME WHEN 'methodology_extraction_results' THEN 'METHODOLOGY_EXTRACTION_RESULT' WHEN 'methodology_result_factual_outputs' THEN 'METHODOLOGY_FACTUAL_OUTPUT' END),NEW.tenant_id,NEW.deal_id,NEW.run_id) THEN RETURN NEW; END IF; RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE' USING ERRCODE='23514'; END$$",
)


def _retarget_v21_current_guard_body() -> tuple[str, ...]:
    predecessor_version = "full-strict-v20"
    current_version = "full-strict-v21"
    predecessor_count = sum(
        statement.count(predecessor_version)
        for statement in _TASK7_CURRENT_GUARD_BODY_PREDECESSOR_SQL_V1
    )
    if predecessor_count != 4:
        raise RuntimeError("TASK7_V21_CURRENT_GUARD_VERSION_SOURCE_INVALID")
    current = tuple(
        statement.replace(predecessor_version, current_version)
        for statement in _TASK7_CURRENT_GUARD_BODY_PREDECESSOR_SQL_V1
    )
    if (
        any(predecessor_version in statement for statement in current)
        or sum(statement.count(current_version) for statement in current) != 4
    ):
        raise RuntimeError("TASK7_V21_CURRENT_GUARD_VERSION_RETARGET_INVALID")
    return current


TASK7_CURRENT_GUARD_BODY_SQL_V1: Final = _retarget_v21_current_guard_body()

TASK7_CURRENT_GUARD_ACL_INSTALL_SQL_V1: Final = (
    "DO $$DECLARE guard_oid oid; BEGIN SELECT oid INTO guard_oid FROM pg_catalog.pg_roles WHERE rolname='idis_task7_guard_function_owner'; IF guard_oid IS NULL OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=guard_oid OR m.roleid=guard_oid) OR EXISTS (SELECT 1 FROM pg_catalog.pg_database d WHERE d.datdba=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(d.datacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspowner=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(n.nspacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.relowner=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(c.relacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute at WHERE at.attnum>0 AND NOT at.attisdropped AND EXISTS (SELECT 1 FROM pg_catalog.aclexplode(at.attacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p WHERE p.proowner=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl d WHERE d.defaclrole=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(d.defaclacl) a WHERE a.grantee=guard_oid)) THEN RAISE EXCEPTION 'TASK7_GUARD_FUNCTION_OWNER_NOT_CLEAN' USING ERRCODE='42501'; END IF; END$$",
    "DO $$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname='idis_task7_guard_function_owner' AND NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolinherit AND NOT r.rolbypassrls AND NOT r.rolreplication) OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member JOIN pg_catalog.pg_roles granted_role ON granted_role.oid=m.roleid WHERE member_role.rolname='idis_task7_guard_function_owner' OR granted_role.rolname='idis_task7_guard_function_owner') THEN RAISE EXCEPTION 'TASK7_GUARD_FUNCTION_OWNER_POSTURE_INVALID' USING ERRCODE='42501'; END IF; END$$",
    "DO $$BEGIN IF EXISTS (SELECT 1 FROM (VALUES ('public.idis_prevent_strict_preflight_proof_mutation()','0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d2027494e5345525427205448454e0a202020202020202020202020202020204946204e45572e636f6e73756d65645f6174204953204e554c4c0a20202020202020202020202020202020202020414e44204e45572e636f6e73756d65645f62795f72756e5f6964204953204e554c4c0a202020202020202020202020202020205448454e0a20202020202020202020202020202020202020202d2d20486f6c642074686520706172656e7420726f77207468726f7567682070726f6f662d696e7365727420636f6d6d69742e2020576974686f757420746869730a20202020202020202020202020202020202020202d2d206c6f636b2c206120636f6e63757272656e74206465616c206572617375726520636f756c6420636f6d6d6974206265747765656e206120706c61696e0a20202020202020202020202020202020202020202d2d206578697374656e636520636865636b20616e64207468652070726f6f6620696e736572742c206372656174696e6720616e206f727068616e2e0a2020202020202020202020202020202020202020504552464f524d20310a202020202020202020202020202020202020202046524f4d206465616c730a20202020202020202020202020202020202020205748455245206465616c732e74656e616e745f6964203d204e45572e74656e616e745f69640a20202020202020202020202020202020202020202020414e44206465616c732e6465616c5f6964203d204e45572e6465616c5f69640a2020202020202020202020202020202020202020464f52204b45592053484152453b0a2020202020202020202020202020202020202020494620464f554e44205448454e0a20202020202020202020202020202020202020202020202052455455524e204e45573b0a2020202020202020202020202020202020202020454e442049463b0a20202020202020202020202020202020454e442049463b0a0a20202020202020202020202020202020524149534520455843455054494f4e0a20202020202020202020202020202020202020202750726f6f6620696e7365727420726571756972657320756e636f6e73756d656420737461746520616e642061206c6976652073616d652d74656e616e74206465616c273b0a202020202020202020202020454c5349462054475f4f50203d202755504441544527205448454e0a202020202020202020202020202020204946204e45572e70726f6f665f6964204953204e4f542044495354494e43542046524f4d204f4c442e70726f6f665f69640a20202020202020202020202020202020202020414e44204e45572e74656e616e745f6964204953204e4f542044495354494e43542046524f4d204f4c442e74656e616e745f69640a20202020202020202020202020202020202020414e44204e45572e6465616c5f6964204953204e4f542044495354494e43542046524f4d204f4c442e6465616c5f69640a20202020202020202020202020202020202020414e44204e45572e636f727075735f68617368204953204e4f542044495354494e43542046524f4d204f4c442e636f727075735f686173680a20202020202020202020202020202020202020414e44204e45572e7374726963745f70726f66696c655f67656e65726174696f6e0a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e7374726963745f70726f66696c655f67656e65726174696f6e0a20202020202020202020202020202020202020414e44204e45572e7374726963745f70726f66696c655f686d6163204953204e4f542044495354494e43542046524f4d204f4c442e7374726963745f70726f66696c655f686d61630a20202020202020202020202020202020202020414e44204e45572e706970656c696e655f636f6e74726163745f76657273696f6e0a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e706970656c696e655f636f6e74726163745f76657273696f6e0a20202020202020202020202020202020202020414e44204e45572e706970656c696e655f636f6e74726163745f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e706970656c696e655f636f6e74726163745f686173680a20202020202020202020202020202020202020414e44204e45572e65787065637465645f636f6e73756d65725f656467655f72756c655f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e65787065637465645f636f6e73756d65725f656467655f72756c655f686173680a20202020202020202020202020202020202020414e44204e45572e7374617469635f6465636973696f6e5f7365745f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e7374617469635f6465636973696f6e5f7365745f686173680a20202020202020202020202020202020202020414e44204e45572e6d6574686f646f6c6f67795f76657273696f6e5f6861736865730a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e6d6574686f646f6c6f67795f76657273696f6e5f6861736865730a20202020202020202020202020202020202020414e44204e45572e70726f6d6f7465645f706f696e7465725f68617368204953204e4f542044495354494e43542046524f4d204f4c442e70726f6d6f7465645f706f696e7465725f686173680a20202020202020202020202020202020202020414e44204e45572e70726f76696465725f726563656970745f7365745f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e70726f76696465725f726563656970745f7365745f686173680a20202020202020202020202020202020202020414e44204e45572e646570656e64656e63795f726563656970745f7365745f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e646570656e64656e63795f726563656970745f7365745f686173680a20202020202020202020202020202020202020414e44204e45572e62696e64696e675f68617368204953204e4f542044495354494e43542046524f4d204f4c442e62696e64696e675f686173680a20202020202020202020202020202020202020414e44204e45572e637265617465645f6174204953204e4f542044495354494e43542046524f4d204f4c442e637265617465645f61740a20202020202020202020202020202020202020414e44204e45572e657870697265735f6174204953204e4f542044495354494e43542046524f4d204f4c442e657870697265735f61740a20202020202020202020202020202020202020414e44204f4c442e636f6e73756d65645f6174204953204e554c4c0a20202020202020202020202020202020202020414e44204f4c442e636f6e73756d65645f62795f72756e5f6964204953204e554c4c0a20202020202020202020202020202020202020414e44204f4c442e657870697265735f6174203e2073746174656d656e745f74696d657374616d7028290a20202020202020202020202020202020202020414e44204e45572e636f6e73756d65645f6174204953204e4f54204e554c4c0a20202020202020202020202020202020202020414e44204e45572e636f6e73756d65645f6174203c3d204f4c442e657870697265735f61740a20202020202020202020202020202020202020414e44204e45572e636f6e73756d65645f62795f72756e5f6964204953204e4f54204e554c4c0a20202020202020202020202020202020202020414e442045584953545320280a202020202020202020202020202020202020202020202053454c45435420310a202020202020202020202020202020202020202020202046524f4d2072756e730a202020202020202020202020202020202020202020202057484552452072756e732e72756e5f6964203d204e45572e636f6e73756d65645f62795f72756e5f69640a20202020202020202020202020202020202020202020202020414e442072756e732e74656e616e745f6964203d204e45572e74656e616e745f69640a20202020202020202020202020202020202020202020202020414e442072756e732e6465616c5f6964203d204e45572e6465616c5f69640a20202020202020202020202020202020202020202020202020414e442072756e732e7374726963745f707265666c696768745f70726f6f665f6964203d204e45572e70726f6f665f69640a20202020202020202020202020202020202020290a202020202020202020202020202020205448454e0a202020202020202020202020202020202020202052455455524e204e45573b0a20202020202020202020202020202020454e442049463b0a0a20202020202020202020202020202020524149534520455843455054494f4e0a2020202020202020202020202020202020202020275374726963742070726f6f6620696d6d757461626c653b206f6e6c79206669727374207265636970726f63616c20636f6e73756d7074696f6e20697320616c6c6f776564273b0a202020202020202020202020454c5349462054475f4f50203d202744454c45544527205448454e0a202020202020202020202020202020204946204e4f542045584953545320280a202020202020202020202020202020202020202053454c45435420310a202020202020202020202020202020202020202046524f4d206465616c730a20202020202020202020202020202020202020205748455245206465616c732e74656e616e745f6964203d204f4c442e74656e616e745f69640a20202020202020202020202020202020202020202020414e44206465616c732e6465616c5f6964203d204f4c442e6465616c5f69640a20202020202020202020202020202020290a202020202020202020202020202020205448454e0a202020202020202020202020202020202020202052455455524e204f4c443b0a20202020202020202020202020202020454e442049463b0a0a20202020202020202020202020202020524149534520455843455054494f4e0a20202020202020202020202020202020202020202753747269637420707265666c696768742070726f6f662063616e6e6f742062652064656c65746564207768696c65206974732073616d652d74656e616e74206465616c20657869737473273b0a202020202020202020202020454e442049463b0a0a202020202020202020202020524149534520455843455054494f4e0a2020202020202020202020202020202027556e737570706f727465642073747269637420707265666c696768742070726f6f66206d75746174696f6e273b0a2020202020202020454e443b0a2020202020202020'),('public.idis_task5_selection_guard()','0a20202020202020204445434c4152450a202020202020202020202020636f6d6d65726369616c5f636f756e7420696e74656765723b0a20202020202020202020202066696e616e6369616c5f636f756e7420696e74656765723b0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d202744454c4554452720414e442045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d20657261737572655f726571756573747320650a20202020202020202020202020202020574845524520652e74656e616e745f6964203d204f4c442e74656e616e745f69640a202020202020202020202020202020202020414e4420652e6465616c5f6964203d204f4c442e6465616c5f69640a202020202020202020202020202020202020414e4420652e737461747573203d2027524551554553544544270a20202020202020202020202029205448454e0a2020202020202020202020202020202052455455524e204f4c443b0a202020202020202020202020454e442049463b0a20202020202020202020202049462054475f4f50203c3e2027494e5345525427205448454e0a20202020202020202020202020202020524149534520455843455054494f4e202752554e5f4d4554484f444f4c4f47595f53454c454354494f4e5f494d4d555441424c45270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a20202020202020202020202053454c454354207175657374696f6e5f636f756e7420494e544f20636f6d6d65726369616c5f636f756e740a20202020202020202020202046524f4d206d6574686f646f6c6f67795f76657273696f6e730a20202020202020202020202057484552452074656e616e745f6964203d204e45572e74656e616e745f69640a2020202020202020202020202020414e44206d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d204e45572e636f6d6d65726369616c5f64645f76657273696f6e5f7265636f72645f69640a2020202020202020202020202020414e44206d6574686f646f6c6f67795f76657273696f6e5f6964203d204e45572e636f6d6d65726369616c5f6d6574686f646f6c6f67795f76657273696f6e5f69640a2020202020202020202020202020414e442076657273696f6e5f68617368203d204e45572e636f6d6d65726369616c5f76657273696f6e5f686173680a2020202020202020202020202020414e44206d6574686f646f6c6f67795f74797065203d2027636f6d6d65726369616c5f6464270a2020202020202020202020202020414e4420737461747573203d202746524f5a454e273b0a20202020202020202020202053454c454354207175657374696f6e5f636f756e7420494e544f2066696e616e6369616c5f636f756e740a20202020202020202020202046524f4d206d6574686f646f6c6f67795f76657273696f6e730a20202020202020202020202057484552452074656e616e745f6964203d204e45572e74656e616e745f69640a2020202020202020202020202020414e44206d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d204e45572e66696e616e6369616c5f64645f76657273696f6e5f7265636f72645f69640a2020202020202020202020202020414e44206d6574686f646f6c6f67795f76657273696f6e5f6964203d204e45572e66696e616e6369616c5f6d6574686f646f6c6f67795f76657273696f6e5f69640a2020202020202020202020202020414e442076657273696f6e5f68617368203d204e45572e66696e616e6369616c5f76657273696f6e5f686173680a2020202020202020202020202020414e44206d6574686f646f6c6f67795f74797065203d202766696e616e6369616c5f6464270a2020202020202020202020202020414e4420737461747573203d202746524f5a454e273b0a202020202020202020202020494620636f6d6d65726369616c5f636f756e742049532044495354494e43542046524f4d2032310a2020202020202020202020202020204f522066696e616e6369616c5f636f756e742049532044495354494e43542046524f4d2037390a2020202020202020202020202020204f5220636f6d6d65726369616c5f636f756e74202b2066696e616e6369616c5f636f756e74203c3e204e45572e73656c65637465645f7175657374696f6e5f636f756e74205448454e0a20202020202020202020202020202020524149534520455843455054494f4e202752554e5f4d4554484f444f4c4f47595f53454c454354494f4e5f494e56414c4944270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a20202020202020202020202052455455524e204e45573b0a2020202020202020454e440a2020202020202020'),('public.idis_task5_coverage_scope_guard()','0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d202744454c45544527205448454e0a2020202020202020202020202020202052455455524e204f4c443b0a202020202020202020202020454e442049463b0a20202020202020202020202049462054475f4f50203d20275550444154452720414e4420280a202020202020202020202020202020204e45572e636f7665726167655f7265636f72645f69642049532044495354494e43542046524f4d204f4c442e636f7665726167655f7265636f72645f69640a202020202020202020202020202020204f52204e45572e74656e616e745f69642049532044495354494e43542046524f4d204f4c442e74656e616e745f69640a202020202020202020202020202020204f52204e45572e6465616c5f69642049532044495354494e43542046524f4d204f4c442e6465616c5f69640a202020202020202020202020202020204f52204e45572e72756e5f69642049532044495354494e43542046524f4d204f4c442e72756e5f69640a202020202020202020202020202020204f52204e45572e73656c656374696f6e5f69642049532044495354494e43542046524f4d204f4c442e73656c656374696f6e5f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f69642049532044495354494e43542046524f4d0a202020202020202020202020202020202020204f4c442e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f76657273696f6e5f69642049532044495354494e43542046524f4d204f4c442e6d6574686f646f6c6f67795f76657273696f6e5f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f7175657374696f6e5f7265636f72645f69642049532044495354494e43542046524f4d0a202020202020202020202020202020202020204f4c442e6d6574686f646f6c6f67795f7175657374696f6e5f7265636f72645f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f7175657374696f6e5f69642049532044495354494e43542046524f4d204f4c442e6d6574686f646f6c6f67795f7175657374696f6e5f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f747970652049532044495354494e43542046524f4d204f4c442e6d6574686f646f6c6f67795f747970650a202020202020202020202020202020204f52204e45572e637265617465645f61742049532044495354494e43542046524f4d204f4c442e637265617465645f61740a20202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f434f5645524147455f53434f50455f494d4d555441424c45270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204e4f542045584953545320280a2020202020202020202020202020202053454c45435420310a2020202020202020202020202020202046524f4d2072756e5f6d6574686f646f6c6f67795f73656c656374696f6e7320730a202020202020202020202020202020204a4f494e206d6574686f646f6c6f67795f7175657374696f6e7320710a2020202020202020202020202020202020204f4e20712e74656e616e745f6964203d204e45572e74656e616e745f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d204e45572e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f76657273696f6e5f6964203d204e45572e6d6574686f646f6c6f67795f76657273696f6e5f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f7175657374696f6e5f7265636f72645f6964203d204e45572e6d6574686f646f6c6f67795f7175657374696f6e5f7265636f72645f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f7175657374696f6e5f6964203d204e45572e6d6574686f646f6c6f67795f7175657374696f6e5f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f74797065203d204e45572e6d6574686f646f6c6f67795f747970650a20202020202020202020202020202020574845524520732e74656e616e745f6964203d204e45572e74656e616e745f69640a202020202020202020202020202020202020414e4420732e6465616c5f6964203d204e45572e6465616c5f69640a202020202020202020202020202020202020414e4420732e72756e5f6964203d204e45572e72756e5f69640a202020202020202020202020202020202020414e4420732e73656c656374696f6e5f6964203d204e45572e73656c656374696f6e5f69640a202020202020202020202020202020202020414e4420280a202020202020202020202020202020202020202028712e6d6574686f646f6c6f67795f74797065203d2027636f6d6d65726369616c5f6464270a202020202020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d20732e636f6d6d65726369616c5f64645f76657273696f6e5f7265636f72645f6964290a20202020202020202020202020202020202020204f520a202020202020202020202020202020202020202028712e6d6574686f646f6c6f67795f74797065203d202766696e616e6369616c5f6464270a202020202020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d20732e66696e616e6369616c5f64645f76657273696f6e5f7265636f72645f6964290a202020202020202020202020202020202020290a20202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f434f5645524147455f53434f50455f494e56414c4944270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a2020202020202020202020204e45572e757064617465645f6174203a3d206e6f7728293b0a20202020202020202020202052455455524e204e45573b0a2020202020202020454e440a2020202020202020'),('public.idis_task6_erasure_requested(uuid,uuid)','0a20202020202020202020202053454c4543542045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d20657261737572655f726571756573747320650a20202020202020202020202020202020574845524520652e74656e616e745f6964203d20636865636b65645f74656e616e740a202020202020202020202020202020202020414e4420652e6465616c5f6964203d20636865636b65645f6465616c0a202020202020202020202020202020202020414e4420652e737461747573203d2027524551554553544544270a202020202020202020202020290a2020202020202020'),('public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)','0a20202020202020204445434c4152450a20202020202020202020202063757272656e745f74656e616e745f696420555549443b0a20202020202020202020202063757272656e745f6465616c5f696420555549443b0a20202020202020202020202063757272656e745f72756e5f696420555549443b0a20202020202020202020202063757272656e745f6f776e65725f696420555549443b0a20202020202020202020202063757272656e745f66656e63696e675f746f6b656e20424947494e543b0a2020202020202020424547494e0a202020202020202020202020424547494e0a2020202020202020202020202020202063757272656e745f74656e616e745f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f74656e616e745f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f6465616c5f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f6465616c5f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f72756e5f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f72756e5f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f6f776e65725f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f6f776e65725f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f66656e63696e675f746f6b656e203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f66656e63696e675f746f6b656e272c2074727565292c2027270a20202020202020202020202020202020293a3a626967696e743b0a202020202020202020202020455843455054494f4e205748454e204f5448455253205448454e0a2020202020202020202020202020202052455455524e2066616c73653b0a202020202020202020202020454e443b0a20202020202020202020202052455455524e2045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d2072756e7320720a20202020202020202020202020202020574845524520722e74656e616e745f6964203d20636865636b65645f74656e616e740a202020202020202020202020202020202020414e4420722e6465616c5f6964203d20636865636b65645f6465616c0a202020202020202020202020202020202020414e4420722e72756e5f6964203d20636865636b65645f72756e0a202020202020202020202020202020202020414e4420722e737461747573203d202752554e4e494e47270a202020202020202020202020202020202020414e4420722e74656e616e745f6964203d2063757272656e745f74656e616e745f69640a202020202020202020202020202020202020414e4420722e6465616c5f6964203d2063757272656e745f6465616c5f69640a202020202020202020202020202020202020414e4420722e72756e5f6964203d2063757272656e745f72756e5f69640a202020202020202020202020202020202020414e4420722e6c656173655f6f776e65725f6964203d2063757272656e745f6f776e65725f69640a202020202020202020202020202020202020414e4420722e6c656173655f66656e63696e675f746f6b656e203d2063757272656e745f66656e63696e675f746f6b656e0a202020202020202020202020202020202020414e4420722e6c656173655f657870697265735f6174203e2073746174656d656e745f74696d657374616d7028290a202020202020202020202020293b0a2020202020202020454e440a2020202020202020'),('public.idis_task6_immutable_guard()','0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d2027494e5345525427205448454e0a202020202020202020202020202020204946204e4f5420696469735f7461736b365f63757272656e745f6c656173655f69735f76616c6964280a20202020202020202020202020202020202020204e45572e74656e616e745f69642c204e45572e6465616c5f69642c204e45572e72756e5f69640a2020202020202020202020202020202029205448454e0a2020202020202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a20202020202020202020202020202020454e442049463b0a2020202020202020202020202020202052455455524e204e45573b0a202020202020202020202020454e442049463b0a20202020202020202020202049462054475f4f50203d202744454c455445270a202020202020202020202020202020414e4420696469735f7461736b365f657261737572655f726571756573746564284f4c442e74656e616e745f69642c204f4c442e6465616c5f696429205448454e0a2020202020202020202020202020202052455455524e204f4c443b0a202020202020202020202020454e442049463b0a202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f45585452414354494f4e5f5245434f52445f494d4d555441424c45270a202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a2020202020202020454e440a2020202020202020'),('public.idis_task6_attempt_guard()','0a20202020202020204445434c4152450a20202020202020202020202063757272656e745f74656e616e745f696420555549443b0a20202020202020202020202063757272656e745f6465616c5f696420555549443b0a20202020202020202020202063757272656e745f72756e5f696420555549443b0a20202020202020202020202063757272656e745f6f776e65725f696420555549443b0a20202020202020202020202063757272656e745f66656e63696e675f746f6b656e20424947494e543b0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d202744454c45544527205448454e0a20202020202020202020202020202020494620696469735f7461736b365f657261737572655f726571756573746564284f4c442e74656e616e745f69642c204f4c442e6465616c5f696429205448454e0a202020202020202020202020202020202020202052455455524e204f4c443b0a20202020202020202020202020202020454e442049463b0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f45585452414354494f4e5f415454454d50545f494d4d555441424c45270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a202020202020202020202020424547494e0a2020202020202020202020202020202063757272656e745f74656e616e745f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f74656e616e745f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f6465616c5f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f6465616c5f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f72756e5f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f72756e5f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f6f776e65725f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f6f776e65725f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f66656e63696e675f746f6b656e203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f66656e63696e675f746f6b656e272c2074727565292c2027270a20202020202020202020202020202020293a3a626967696e743b0a202020202020202020202020455843455054494f4e205748454e204f5448455253205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e443b0a2020202020202020202020204946204e4f542045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d2072756e7320720a20202020202020202020202020202020574845524520722e74656e616e745f6964203d204e45572e74656e616e745f69640a202020202020202020202020202020202020414e4420722e6465616c5f6964203d204e45572e6465616c5f69640a202020202020202020202020202020202020414e4420722e72756e5f6964203d204e45572e72756e5f69640a202020202020202020202020202020202020414e4420722e737461747573203d202752554e4e494e47270a202020202020202020202020202020202020414e4420722e74656e616e745f6964203d2063757272656e745f74656e616e745f69640a202020202020202020202020202020202020414e4420722e6465616c5f6964203d2063757272656e745f6465616c5f69640a202020202020202020202020202020202020414e4420722e72756e5f6964203d2063757272656e745f72756e5f69640a202020202020202020202020202020202020414e4420722e6c656173655f6f776e65725f6964203d2063757272656e745f6f776e65725f69640a202020202020202020202020202020202020414e4420722e6c656173655f66656e63696e675f746f6b656e203d2063757272656e745f66656e63696e675f746f6b656e0a202020202020202020202020202020202020414e4420722e6c656173655f657870697265735f6174203e2073746174656d656e745f74696d657374616d7028290a20202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204e4f542045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d2072756e5f737465707320730a20202020202020202020202020202020574845524520732e74656e616e745f6964203d204e45572e74656e616e745f69640a202020202020202020202020202020202020414e4420732e72756e5f6964203d204e45572e72756e5f69640a202020202020202020202020202020202020414e4420732e737465705f6964203d204e45572e72756e5f737465705f69640a202020202020202020202020202020202020414e4420732e737465705f6e616d65203d20274d4554484f444f4c4f47595f45585452414354494f4e5f5441534b5f455845435554494f4e270a202020202020202020202020202020202020414e4420732e737465705f6f72646572203d20360a202020202020202020202020202020202020414e4420732e737461747573203d202752554e4e494e47270a20202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e442049463b0a20202020202020202020202049462054475f4f50203d2027494e5345525427205448454e0a202020202020202020202020202020204946204e45572e737461747573203c3e20275354415254454427205448454e0a2020202020202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f415454454d50545f4d5553545f5354415254270a2020202020202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a20202020202020202020202020202020454e442049463b0a202020202020202020202020202020204946204e45572e6c656173655f6f776e65725f6964203c3e2063757272656e745f6f776e65725f69640a202020202020202020202020202020202020204f52204e45572e6c656173655f66656e63696e675f746f6b656e203c3e2063757272656e745f66656e63696e675f746f6b656e205448454e0a2020202020202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a20202020202020202020202020202020454e442049463b0a2020202020202020202020202020202052455455524e204e45573b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204f4c442e737461747573203c3e202753544152544544270a2020202020202020202020202020204f52204e45572e737461747573204e4f5420494e202827535543434545444544272c20274641494c4544272c20274f5554434f4d455f554e4b4e4f574e27290a2020202020202020202020202020204f522028746f5f6a736f6e62284e455729202d2041525241595b0a202020202020202020202020202020202020202027737461747573272c20276f627365727665645f6d6f64656c272c202770726f76696465725f726571756573745f6964272c0a20202020202020202020202020202020202020202770726f76696465725f726573706f6e73655f736861323536272c2027646973706174636865645f63616c6c5f736861323536272c0a202020202020202020202020202020202020202027636f6d706c657465645f6174272c20276c6174656e63795f6d73272c0a202020202020202020202020202020202020202027696e7075745f746f6b656e73272c20276f75747075745f746f6b656e73272c202773746f705f726561736f6e272c0a2020202020202020202020202020202020202020276275646765745f7465726d696e616c5f7374617465272c2027726563656970745f736861323536272c2027736166655f6661696c7572655f636f6465270a2020202020202020202020202020202020205d3a3a746578745b5d290a20202020202020202020202020202020202049532044495354494e43542046524f4d0a20202020202020202020202020202020202028746f5f6a736f6e62284f4c4429202d2041525241595b0a202020202020202020202020202020202020202027737461747573272c20276f627365727665645f6d6f64656c272c202770726f76696465725f726571756573745f6964272c0a20202020202020202020202020202020202020202770726f76696465725f726573706f6e73655f736861323536272c2027646973706174636865645f63616c6c5f736861323536272c0a202020202020202020202020202020202020202027636f6d706c657465645f6174272c20276c6174656e63795f6d73272c0a202020202020202020202020202020202020202027696e7075745f746f6b656e73272c20276f75747075745f746f6b656e73272c202773746f705f726561736f6e272c0a2020202020202020202020202020202020202020276275646765745f7465726d696e616c5f7374617465272c2027726563656970745f736861323536272c2027736166655f6661696c7572655f636f6465270a2020202020202020202020202020202020205d3a3a746578745b5d29205448454e0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f45585452414354494f4e5f415454454d50545f494d4d555441424c45270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204e45572e73746174757320494e202827535543434545444544272c20274641494c454427290a202020202020202020202020202020414e4420280a202020202020202020202020202020202020202063757272656e745f6f776e65725f6964203c3e204f4c442e6c656173655f6f776e65725f69640a20202020202020202020202020202020202020204f522063757272656e745f66656e63696e675f746f6b656e203c3e204f4c442e6c656173655f66656e63696e675f746f6b656e0a20202020202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204e45572e737461747573203d20274f5554434f4d455f554e4b4e4f574e270a202020202020202020202020202020414e44204e4f5420280a2020202020202020202020202020202020202020280a20202020202020202020202020202020202020202020202063757272656e745f6f776e65725f6964203d204f4c442e6c656173655f6f776e65725f69640a202020202020202020202020202020202020202020202020414e442063757272656e745f66656e63696e675f746f6b656e203d204f4c442e6c656173655f66656e63696e675f746f6b656e0a2020202020202020202020202020202020202020290a20202020202020202020202020202020202020204f522063757272656e745f66656e63696e675f746f6b656e203e204f4c442e6c656173655f66656e63696e675f746f6b656e0a20202020202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e442049463b0a20202020202020202020202052455455524e204e45573b0a2020202020202020454e440a2020202020202020'),('public.idis_task6_validate_result_outputs()','0a20202020202020204445434c4152450a202020202020202020202020636865636b65645f726573756c7420555549443b0a202020202020202020202020636865636b65645f6f7574636f6d6520544558543b0a2020202020202020202020206f75747075745f636f756e7420424947494e543b0a2020202020202020424547494e0a202020202020202020202020636865636b65645f726573756c74203a3d20434f414c45534345284e45572e726573756c745f69642c204f4c442e726573756c745f6964293b0a20202020202020202020202053454c454354206f7574636f6d6520494e544f20636865636b65645f6f7574636f6d650a20202020202020202020202046524f4d206d6574686f646f6c6f67795f65787472616374696f6e5f726573756c74730a202020202020202020202020574845524520726573756c745f6964203d20636865636b65645f726573756c743b0a202020202020202020202020494620636865636b65645f6f7574636f6d65204953204e554c4c205448454e0a2020202020202020202020202020202052455455524e204e554c4c3b0a202020202020202020202020454e442049463b0a20202020202020202020202053454c45435420636f756e74282a2920494e544f206f75747075745f636f756e740a20202020202020202020202046524f4d206d6574686f646f6c6f67795f726573756c745f6661637475616c5f6f7574707574730a202020202020202020202020574845524520726573756c745f6964203d20636865636b65645f726573756c743b0a20202020202020202020202049462028636865636b65645f6f7574636f6d65203d202745564944454e43455f464f554e442720414e44206f75747075745f636f756e74203d2030290a2020202020202020202020202020204f522028636865636b65645f6f7574636f6d65203d202745564944454e43455f4e4f545f464f554e442720414e44206f75747075745f636f756e74203c3e203029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f524553554c545f4641435455414c5f4f55545055545f4d49534d41544348270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a20202020202020202020202052455455524e204e554c4c3b0a2020202020202020454e440a2020202020202020')) AS expected(function_signature,prosrc_utf8_hex) LEFT JOIN pg_catalog.pg_proc p ON p.oid=pg_catalog.to_regprocedure(expected.function_signature) WHERE p.oid IS NULL OR p.proowner<>(current_user)::pg_catalog.regrole OR p.prosrc<>pg_catalog.convert_from(pg_catalog.decode(expected.prosrc_utf8_hex,'hex'),'UTF8') OR p.prosecdef OR p.proconfig IS NOT NULL OR p.proacl IS NOT NULL) THEN RAISE EXCEPTION 'TASK7_RETAINED_0037_CATALOG_BASELINE_INVALID' USING ERRCODE='42501'; END IF; END$$",
    "REVOKE ALL ON SCHEMA public FROM idis_task7_guard_function_owner",
    "GRANT USAGE ON SCHEMA public TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.source_lineage_objects FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (content_sha256,corpus_receipt_id,corpus_receipt_sha256,deal_id,object_identity_sha256,object_kind,source_disposition,source_lineage_object_id,source_version,tenant_id,write_origin) ON TABLE public.source_lineage_objects FROM idis_task7_guard_function_owner",
    "GRANT SELECT (content_sha256,corpus_receipt_id,corpus_receipt_sha256,deal_id,object_identity_sha256,object_kind,source_disposition,source_lineage_object_id,source_version,tenant_id,write_origin) ON TABLE public.source_lineage_objects TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.data_room_packages FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,metadata,package_id,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,status,tenant_id) ON TABLE public.data_room_packages FROM idis_task7_guard_function_owner",
    "GRANT SELECT (deal_id,metadata,package_id,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,status,tenant_id) ON TABLE public.data_room_packages TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.data_room_package_files FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,doc_id,document_id,error_codes,extension,file_entry_id,file_status,package_id,parse_status,path_hash,reason_codes,sequence,sha256,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,storage_uri,support_status,tenant_id,triage_status) ON TABLE public.data_room_package_files FROM idis_task7_guard_function_owner",
    "GRANT SELECT (deal_id,doc_id,document_id,error_codes,extension,file_entry_id,file_status,package_id,parse_status,path_hash,reason_codes,sequence,sha256,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,storage_uri,support_status,tenant_id,triage_status) ON TABLE public.data_room_package_files TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_artifacts FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,doc_id,doc_type,ingested_at,metadata,sha256,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,source_system,tenant_id,title,uri,version_id) ON TABLE public.document_artifacts FROM idis_task7_guard_function_owner",
    "GRANT SELECT (deal_id,doc_id,doc_type,ingested_at,metadata,sha256,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,source_system,tenant_id,title,uri,version_id) ON TABLE public.document_artifacts TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.documents FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,doc_id,doc_type,document_id,metadata,parse_status,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,tenant_id) ON TABLE public.documents FROM idis_task7_guard_function_owner",
    "GRANT SELECT (deal_id,doc_id,doc_type,document_id,metadata,parse_status,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,tenant_id) ON TABLE public.documents TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_parse_attempts FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (attempt_ordinal,deal_id,document_id,finished_at,input_sha256,parse_attempt_id,parser_name,parser_version,receipt_sha256,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,started_at,status,tenant_id,write_origin) ON TABLE public.document_parse_attempts FROM idis_task7_guard_function_owner",
    "GRANT SELECT (attempt_ordinal,deal_id,document_id,finished_at,input_sha256,parse_attempt_id,parser_name,parser_version,receipt_sha256,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,started_at,status,tenant_id,write_origin) ON TABLE public.document_parse_attempts TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_spans FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (content_hash,deal_id,document_id,locator,parse_attempt_id,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,span_id,span_type,tenant_id) ON TABLE public.document_spans FROM idis_task7_guard_function_owner",
    "GRANT SELECT (content_hash,deal_id,document_id,locator,parse_attempt_id,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,span_id,span_type,tenant_id) ON TABLE public.document_spans TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_output_objects FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,native_identity_sha256,native_output_sha256,output_class,output_kind,output_object_id,pipeline_contract_hash,pipeline_contract_version,run_id,tenant_id) ON TABLE public.run_output_objects FROM idis_task7_guard_function_owner",
    "GRANT SELECT (deal_id,native_identity_sha256,native_output_sha256,output_class,output_kind,output_object_id,pipeline_contract_hash,pipeline_contract_version,run_id,tenant_id) ON TABLE public.run_output_objects TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_coverage_records FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (coverage_record_id,deal_id,methodology_question_id,methodology_question_record_id,methodology_type,methodology_version_id,methodology_version_record_id,run_id,run_output_object_id,run_output_object_kind,selection_id,tenant_id) ON TABLE public.methodology_coverage_records FROM idis_task7_guard_function_owner",
    "GRANT SELECT (coverage_record_id,deal_id,methodology_question_id,methodology_question_record_id,methodology_type,methodology_version_id,methodology_version_record_id,run_id,run_output_object_id,run_output_object_kind,selection_id,tenant_id) ON TABLE public.methodology_coverage_records TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_tasks FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (candidate_span_set_sha256,coverage_record_id,deal_id,document_classification_id,document_classification_sha256,document_id,extraction_task_id,methodology_question_id,methodology_question_record_id,methodology_type,methodology_version_id,methodology_version_record_id,planner_contract_version,run_id,run_output_object_id,run_output_object_kind,selection_id,task_contract_version,task_input_sha256,task_record_id,tenant_id) ON TABLE public.methodology_extraction_tasks FROM idis_task7_guard_function_owner",
    "GRANT SELECT (candidate_span_set_sha256,coverage_record_id,deal_id,document_classification_id,document_classification_sha256,document_id,extraction_task_id,methodology_question_id,methodology_question_record_id,methodology_type,methodology_version_id,methodology_version_record_id,planner_contract_version,run_id,run_output_object_id,run_output_object_kind,selection_id,task_contract_version,task_input_sha256,task_record_id,tenant_id) ON TABLE public.methodology_extraction_tasks TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_results FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (attempt_id,deal_id,gap_finding,outcome,provider_provenance_sha256,result_id,result_sha256,run_id,run_output_object_id,run_output_object_kind,searched_document_ids,searched_scope_sha256,searched_span_ids,task_input_sha256,task_record_id,tenant_id) ON TABLE public.methodology_extraction_results FROM idis_task7_guard_function_owner",
    "GRANT SELECT (attempt_id,deal_id,gap_finding,outcome,provider_provenance_sha256,result_id,result_sha256,run_id,run_output_object_id,run_output_object_kind,searched_document_ids,searched_scope_sha256,searched_span_ids,task_input_sha256,task_record_id,tenant_id) ON TABLE public.methodology_extraction_results TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_factual_outputs FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (accepted_locator_set_sha256,accepted_locator_sha256s,accepted_source_span_ids,deal_id,factual_output_id,factual_output_record_id,output_ordinal,result_id,run_id,run_output_object_id,run_output_object_kind,task_record_id,tenant_id,typed_output_sha256) ON TABLE public.methodology_result_factual_outputs FROM idis_task7_guard_function_owner",
    "GRANT SELECT (accepted_locator_set_sha256,accepted_locator_sha256s,accepted_source_span_ids,deal_id,factual_output_id,factual_output_record_id,output_ordinal,result_id,run_id,run_output_object_id,run_output_object_kind,task_record_id,tenant_id,typed_output_sha256) ON TABLE public.methodology_result_factual_outputs TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_claim_links FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (canonical_key_sha256,claim_id,claim_link_id,coverage_record_id,deal_id,factual_output_record_id,methodology_question_record_id,methodology_version_record_id,result_id,reused_existing_claim,run_id,run_output_object_id,run_output_object_kind,selection_id,tenant_id) ON TABLE public.methodology_result_claim_links FROM idis_task7_guard_function_owner",
    "GRANT SELECT (canonical_key_sha256,claim_id,claim_link_id,coverage_record_id,deal_id,factual_output_record_id,methodology_question_record_id,methodology_version_record_id,result_id,reused_existing_claim,run_id,run_output_object_id,run_output_object_kind,selection_id,tenant_id) ON TABLE public.methodology_result_claim_links TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_evidence_links FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (claim_link_id,deal_id,document_id,evidence_id,evidence_link_id,factual_output_record_id,locator_sha256,result_id,run_id,run_output_object_id,run_output_object_kind,source_span_id,tenant_id) ON TABLE public.methodology_result_evidence_links FROM idis_task7_guard_function_owner",
    "GRANT SELECT (claim_link_id,deal_id,document_id,evidence_id,evidence_link_id,factual_output_record_id,locator_sha256,result_id,run_id,run_output_object_id,run_output_object_kind,source_span_id,tenant_id) ON TABLE public.methodology_result_evidence_links TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_sanad_links FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (claim_link_id,deal_id,factual_output_record_id,primary_evidence_id,result_id,run_id,run_output_object_id,run_output_object_kind,sanad_id,sanad_link_id,tenant_id) ON TABLE public.methodology_result_sanad_links FROM idis_task7_guard_function_owner",
    "GRANT SELECT (claim_link_id,deal_id,factual_output_record_id,primary_evidence_id,result_id,run_id,run_output_object_id,run_output_object_kind,sanad_id,sanad_link_id,tenant_id) ON TABLE public.methodology_result_sanad_links TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_defect_links FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,defect_id,defect_link_id,factual_output_record_id,result_id,run_id,run_output_object_id,run_output_object_kind,sanad_link_id,tenant_id) ON TABLE public.methodology_result_defect_links FROM idis_task7_guard_function_owner",
    "GRANT SELECT (deal_id,defect_id,defect_link_id,factual_output_record_id,result_id,run_id,run_output_object_id,run_output_object_kind,sanad_link_id,tenant_id) ON TABLE public.methodology_result_defect_links TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deterministic_calculations FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (calc_id,calc_type,code_version,deal_id,formula_hash,inputs,output,reproducibility_hash,run_id,run_output_object_id,run_output_object_kind,tenant_id) ON TABLE public.deterministic_calculations FROM idis_task7_guard_function_owner",
    "GRANT SELECT (calc_id,calc_type,code_version,deal_id,formula_hash,inputs,output,reproducibility_hash,run_id,run_output_object_id,run_output_object_kind,tenant_id) ON TABLE public.deterministic_calculations TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.vector_embeddings FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (content_hash,deal_id,embedding,embedding_dimensions,embedding_id,embedding_model,run_id,run_output_object_id,run_output_object_kind,source_id,source_type,tenant_id) ON TABLE public.vector_embeddings FROM idis_task7_guard_function_owner",
    "GRANT SELECT (content_hash,deal_id,embedding,embedding_dimensions,embedding_id,embedding_model,run_id,run_output_object_id,run_output_object_kind,source_id,source_type,tenant_id) ON TABLE public.vector_embeddings TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.evidence_trust_findings FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (calc_ids,claim_id,court_id,deal_id,defect_ids,evidence_ids,finding_id,finding_type,reason_codes,run_id,run_output_object_id,run_output_object_kind,sanad_id,tenant_id) ON TABLE public.evidence_trust_findings FROM idis_task7_guard_function_owner",
    "GRANT SELECT (calc_ids,claim_id,court_id,deal_id,defect_ids,evidence_ids,finding_id,finding_type,reason_codes,run_id,run_output_object_id,run_output_object_kind,sanad_id,tenant_id) ON TABLE public.evidence_trust_findings TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.muhasabah_records FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (agent_id,confidence,deal_id,is_subjective,output_id,record_id,record_timestamp,run_id,run_output_object_id,run_output_object_kind,source_step,supported_calc_ids,supported_claim_ids,tenant_id,uncertainties) ON TABLE public.muhasabah_records FROM idis_task7_guard_function_owner",
    "GRANT SELECT (agent_id,confidence,deal_id,is_subjective,output_id,record_id,record_timestamp,run_id,run_output_object_id,run_output_object_kind,source_step,supported_calc_ids,supported_claim_ids,tenant_id,uncertainties) ON TABLE public.muhasabah_records TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.validated_evidence_packages FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (court_id,dashboard_id,deal_id,package_id,run_id,run_output_object_id,run_output_object_kind,safe_summary,status,tenant_id) ON TABLE public.validated_evidence_packages FROM idis_task7_guard_function_owner",
    "GRANT SELECT (court_id,dashboard_id,deal_id,package_id,run_id,run_output_object_id,run_output_object_kind,safe_summary,status,tenant_id) ON TABLE public.validated_evidence_packages TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.debate_sessions FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,debate_id,max_rounds,protocol_version,rounds,run_id,run_output_object_id,run_output_object_kind,status,tenant_id) ON TABLE public.debate_sessions FROM idis_task7_guard_function_owner",
    "GRANT SELECT (deal_id,debate_id,max_rounds,protocol_version,rounds,run_id,run_output_object_id,run_output_object_kind,status,tenant_id) ON TABLE public.debate_sessions TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.layer2_ic_challenges FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (challenge_id,deal_id,run_id,run_output_object_id,run_output_object_kind,safe_summary,source_debate_id,status,tenant_id) ON TABLE public.layer2_ic_challenges FROM idis_task7_guard_function_owner",
    "GRANT SELECT (challenge_id,deal_id,run_id,run_output_object_id,run_output_object_kind,safe_summary,source_debate_id,status,tenant_id) ON TABLE public.layer2_ic_challenges TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.layer2_ic_findings FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (category,challenge_id,deal_id,enrichment_ref_ids,finding_id,finding_type,graph_ref_ids,rag_ref_ids,run_id,run_output_object_id,run_output_object_kind,severity,supported_calc_ids,supported_claim_ids,tenant_id) ON TABLE public.layer2_ic_findings FROM idis_task7_guard_function_owner",
    "GRANT SELECT (category,challenge_id,deal_id,enrichment_ref_ids,finding_id,finding_type,graph_ref_ids,rag_ref_ids,run_id,run_output_object_id,run_output_object_kind,severity,supported_calc_ids,supported_claim_ids,tenant_id) ON TABLE public.layer2_ic_findings TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deliverables FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (catalog_ordinal,content_sha256,content_type,deal_id,deliverable_id,deliverable_type,download_proof_hash,format,object_version_hash,run_id,run_output_object_id,run_output_object_kind,size_bytes,status,tenant_id,uri,uri_sha256) ON TABLE public.deliverables FROM idis_task7_guard_function_owner",
    "GRANT SELECT (catalog_ordinal,content_sha256,content_type,deal_id,deliverable_id,deliverable_type,download_proof_hash,format,object_version_hash,run_id,run_output_object_id,run_output_object_kind,size_bytes,status,tenant_id,uri,uri_sha256) ON TABLE public.deliverables TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.calc_sanads FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (calc_grade,calc_id,calc_sanad_id,deal_id,explanation,input_claim_ids,input_min_sanad_grade,run_id,tenant_id) ON TABLE public.calc_sanads FROM idis_task7_guard_function_owner",
    "GRANT SELECT (calc_grade,calc_id,calc_sanad_id,deal_id,explanation,input_claim_ids,input_min_sanad_grade,run_id,tenant_id) ON TABLE public.calc_sanads TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_task_spans FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (content_sha256,deal_id,document_id,locator_sha256,run_id,span_id,span_ordinal,task_record_id,task_span_record_id,tenant_id) ON TABLE public.methodology_extraction_task_spans FROM idis_task7_guard_function_owner",
    "GRANT SELECT (content_sha256,deal_id,document_id,locator_sha256,run_id,span_id,span_ordinal,task_record_id,task_span_record_id,tenant_id) ON TABLE public.methodology_extraction_task_spans TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.runs FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,lease_expires_at,lease_fencing_token,lease_owner_id,pipeline_contract_hash,pipeline_contract_version,pipeline_resume_state,run_id,status,strict_preflight_proof_id,tenant_id) ON TABLE public.runs FROM idis_task7_guard_function_owner",
    "GRANT SELECT (deal_id,lease_expires_at,lease_fencing_token,lease_owner_id,pipeline_contract_hash,pipeline_contract_version,pipeline_resume_state,run_id,status,strict_preflight_proof_id,tenant_id) ON TABLE public.runs TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_steps FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (run_id,status,step_id,step_name,step_order,tenant_id) ON TABLE public.run_steps FROM idis_task7_guard_function_owner",
    "GRANT SELECT (run_id,status,step_id,step_name,step_order,tenant_id) ON TABLE public.run_steps TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_step_outputs FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,output_kind,output_object_id,output_ordinal,run_id,semantic_rule_hash,semantic_rule_version,step_id,step_name,subrole,tenant_id) ON TABLE public.run_step_outputs FROM idis_task7_guard_function_owner",
    "GRANT SELECT (deal_id,output_kind,output_object_id,output_ordinal,run_id,semantic_rule_hash,semantic_rule_version,step_id,step_name,subrole,tenant_id) ON TABLE public.run_step_outputs TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_expected_consumer_edges FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (consumer_step_name,consumer_subrole,deal_id,expected_edge_id,run_id,tenant_id) ON TABLE public.run_expected_consumer_edges FROM idis_task7_guard_function_owner",
    "GRANT SELECT (consumer_step_name,consumer_subrole,deal_id,expected_edge_id,run_id,tenant_id) ON TABLE public.run_expected_consumer_edges TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_consumer_receipts FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (consumer_output_object_id,consumer_step_name,consumer_subrole,deal_id,expected_edge_id,run_id,tenant_id) ON TABLE public.run_consumer_receipts FROM idis_task7_guard_function_owner",
    "GRANT SELECT (consumer_output_object_id,consumer_step_name,consumer_subrole,deal_id,expected_edge_id,run_id,tenant_id) ON TABLE public.run_consumer_receipts TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deliverable_manifest_references FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (catalog_ordinal,deal_id,manifest_deliverable_id,manifest_output_object_id,member_content_sha256,member_identity_sha256,member_kind,member_output_object_id,member_step_output_id,member_value_sha256,reference_sha256,run_id,tenant_id) ON TABLE public.deliverable_manifest_references FROM idis_task7_guard_function_owner",
    "GRANT SELECT (catalog_ordinal,deal_id,manifest_deliverable_id,manifest_output_object_id,member_content_sha256,member_identity_sha256,member_kind,member_output_object_id,member_step_output_id,member_value_sha256,reference_sha256,run_id,tenant_id) ON TABLE public.deliverable_manifest_references TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.strict_preflight_proofs FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (consumed_by_run_id,corpus_hash,deal_id,pipeline_contract_hash,pipeline_contract_version,proof_id,tenant_id) ON TABLE public.strict_preflight_proofs FROM idis_task7_guard_function_owner",
    "GRANT SELECT (consumed_by_run_id,corpus_hash,deal_id,pipeline_contract_hash,pipeline_contract_version,proof_id,tenant_id) ON TABLE public.strict_preflight_proofs TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_methodology_selections FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (commercial_dd_version_record_id,deal_id,financial_dd_version_record_id,run_id,selection_id,tenant_id) ON TABLE public.run_methodology_selections FROM idis_task7_guard_function_owner",
    "GRANT SELECT (commercial_dd_version_record_id,deal_id,financial_dd_version_record_id,run_id,selection_id,tenant_id) ON TABLE public.run_methodology_selections TO idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_questions FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (methodology_question_id,methodology_question_record_id,methodology_type,methodology_version_id,methodology_version_record_id,tenant_id) ON TABLE public.methodology_questions FROM idis_task7_guard_function_owner",
    "GRANT SELECT (methodology_question_id,methodology_question_record_id,methodology_type,methodology_version_id,methodology_version_record_id,tenant_id) ON TABLE public.methodology_questions TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_source_canonical_jsonb_text_v1(jsonb) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_source_canonical_jsonb_text_v1(jsonb) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_source_canonical_jsonb_text_v1(jsonb) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_source_canonical_jsonb_text_v1(jsonb) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_source_hash_jsonb_v1(jsonb) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_source_hash_jsonb_v1(jsonb) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_source_hash_jsonb_v1(jsonb) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_source_hash_jsonb_v1(jsonb) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_sha1_v1(bytea) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_sha1_v1(bytea) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_sha1_v1(bytea) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_sha1_v1(bytea) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_uuidv5_v1(uuid,text) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_uuidv5_v1(uuid,text) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_uuidv5_v1(uuid,text) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_uuidv5_v1(uuid,text) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_source_projection_v1(uuid) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_source_projection_v1(uuid) SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_source_projection_v1(uuid) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_source_projection_v1(uuid) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_source_exact_one_v1() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_source_exact_one_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_current_source_exact_one_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_source_exact_one_v1() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_canonical_jsonb_text_v1(jsonb) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_canonical_jsonb_text_v1(jsonb) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_canonical_jsonb_text_v1(jsonb) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_canonical_jsonb_text_v1(jsonb) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_hash_jsonb_v1(jsonb) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_hash_jsonb_v1(jsonb) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_hash_jsonb_v1(jsonb) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_hash_jsonb_v1(jsonb) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_output_exact_one_v1() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_output_exact_one_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_current_output_exact_one_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_output_exact_one_v1() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_manifest_reference_digest_v1(uuid,uuid,uuid,text,text,uuid,text,integer,uuid,uuid,text,text,text) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_manifest_reference_digest_v1(uuid,uuid,uuid,text,text,uuid,text,integer,uuid,uuid,text,text,text) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_manifest_reference_digest_v1(uuid,uuid,uuid,text,text,uuid,text,integer,uuid,uuid,text,text,text) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_manifest_reference_digest_v1(uuid,uuid,uuid,text,text,uuid,text,integer,uuid,uuid,text,text,text) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_receipt_equality_v1() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_receipt_equality_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_current_receipt_equality_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_receipt_equality_v1() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_input_binding_admission_v1() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_input_binding_admission_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_current_input_binding_admission_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_input_binding_admission_v1() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_expected_edge_admission_is_valid_v1(jsonb,uuid,uuid,uuid) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_expected_edge_admission_is_valid_v1(jsonb,uuid,uuid,uuid) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_expected_edge_admission_is_valid_v1(jsonb,uuid,uuid,uuid) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_expected_edge_admission_is_valid_v1(jsonb,uuid,uuid,uuid) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_append_only_lease_v1() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_append_only_lease_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_current_append_only_lease_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_append_only_lease_v1() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_source_bound_v1() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_source_bound_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_current_source_bound_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_source_bound_v1() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_output_bound_v1() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_output_bound_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_current_output_bound_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_output_bound_v1() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_runs_contract_immutable_v1() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_runs_contract_immutable_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_current_runs_contract_immutable_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_runs_contract_immutable_v1() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_current_run_steps_contract_immutable_v1() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task7_current_run_steps_contract_immutable_v1() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_current_run_steps_contract_immutable_v1() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_run_steps_contract_immutable_v1() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task5_coverage_scope_guard() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task5_coverage_scope_guard() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task5_coverage_scope_guard() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task5_coverage_scope_guard() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task6_immutable_guard() OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task6_immutable_guard() SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task6_immutable_guard() SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task6_immutable_guard() FROM PUBLIC",
    "ALTER FUNCTION public.idis_task6_current_lease_is_valid(uuid,uuid,uuid) OWNER TO idis_task7_guard_function_owner",
    "ALTER FUNCTION public.idis_task6_current_lease_is_valid(uuid,uuid,uuid) SECURITY INVOKER",
    "ALTER FUNCTION public.idis_task6_current_lease_is_valid(uuid,uuid,uuid) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task6_current_lease_is_valid(uuid,uuid,uuid) FROM PUBLIC",
    "ALTER FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) SECURITY DEFINER",
    "ALTER FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) SET search_path TO pg_catalog, pg_temp",
    "REVOKE ALL ON FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) TO idis_task7_guard_function_owner",
)

TASK7_CURRENT_GUARD_ACL_DOWNGRADE_SQL_V1: Final = (
    "DROP TRIGGER ctrg_task7_source_lineage_objects_exact_one ON public.source_lineage_objects",
    "DROP TRIGGER ctrg_task7_data_room_packages_source_exact_one ON public.data_room_packages",
    "DROP TRIGGER ctrg_task7_data_room_package_files_source_exact_one ON public.data_room_package_files",
    "DROP TRIGGER ctrg_task7_document_artifacts_source_exact_one ON public.document_artifacts",
    "DROP TRIGGER ctrg_task7_documents_source_exact_one ON public.documents",
    "DROP TRIGGER ctrg_task7_document_parse_attempts_source_exact_one ON public.document_parse_attempts",
    "DROP TRIGGER ctrg_task7_document_spans_source_exact_one ON public.document_spans",
    "DROP TRIGGER ctrg_task7_run_output_objects_exact_one ON public.run_output_objects",
    "DROP TRIGGER ctrg_task7_methodology_coverage_records_output_exact_one ON public.methodology_coverage_records",
    "DROP TRIGGER ctrg_task7_methodology_extraction_tasks_output_exact_one ON public.methodology_extraction_tasks",
    "DROP TRIGGER ctrg_task7_methodology_extraction_results_output_exact_one ON public.methodology_extraction_results",
    "DROP TRIGGER ctrg_task7_methodology_result_factual_outputs_output_exact_one ON public.methodology_result_factual_outputs",
    "DROP TRIGGER ctrg_task7_methodology_result_claim_links_output_exact_one ON public.methodology_result_claim_links",
    "DROP TRIGGER ctrg_task7_methodology_result_evidence_links_output_exact_one ON public.methodology_result_evidence_links",
    "DROP TRIGGER ctrg_task7_methodology_result_sanad_links_output_exact_one ON public.methodology_result_sanad_links",
    "DROP TRIGGER ctrg_task7_methodology_result_defect_links_output_exact_one ON public.methodology_result_defect_links",
    "DROP TRIGGER ctrg_task7_deterministic_calculations_output_exact_one ON public.deterministic_calculations",
    "DROP TRIGGER ctrg_task7_vector_embeddings_output_exact_one ON public.vector_embeddings",
    "DROP TRIGGER ctrg_task7_evidence_trust_findings_output_exact_one ON public.evidence_trust_findings",
    "DROP TRIGGER ctrg_task7_muhasabah_records_output_exact_one ON public.muhasabah_records",
    "DROP TRIGGER ctrg_task7_validated_evidence_packages_output_exact_one ON public.validated_evidence_packages",
    "DROP TRIGGER ctrg_task7_debate_sessions_output_exact_one ON public.debate_sessions",
    "DROP TRIGGER ctrg_task7_layer2_ic_challenges_output_exact_one ON public.layer2_ic_challenges",
    "DROP TRIGGER ctrg_task7_layer2_ic_findings_output_exact_one ON public.layer2_ic_findings",
    "DROP TRIGGER ctrg_task7_deliverables_output_exact_one ON public.deliverables",
    "DROP TRIGGER ctrg_task7_run_steps_receipt_complete ON public.run_steps",
    "DROP TRIGGER ctrg_task7_run_step_outputs_receipt_complete ON public.run_step_outputs",
    "DROP TRIGGER ctrg_task7_run_output_objects_receipt_complete ON public.run_output_objects",
    "DROP TRIGGER ctrg_task7_expected_edges_decision_complete ON public.run_expected_consumer_edges",
    "DROP TRIGGER ctrg_task7_consumer_receipts_edge_complete ON public.run_consumer_receipts",
    "DROP TRIGGER ctrg_task7_deliverable_manifest_references_exact_one ON public.deliverable_manifest_references",
    "DROP TRIGGER ctrg_task7_deliverable_manifest_references_exact_projection ON public.deliverable_manifest_references",
    "DROP TRIGGER trg_task7_run_input_bindings_append_only ON public.run_input_bindings",
    "DROP TRIGGER trg_task7_run_output_objects_append_only ON public.run_output_objects",
    "DROP TRIGGER trg_task7_run_step_outputs_append_only ON public.run_step_outputs",
    "DROP TRIGGER trg_task7_run_applicability_decisions_append_only ON public.run_applicability_decisions",
    "DROP TRIGGER trg_task7_run_expected_consumer_edges_append_only ON public.run_expected_consumer_edges",
    "DROP TRIGGER trg_task7_run_consumer_receipts_append_only ON public.run_consumer_receipts",
    "DROP TRIGGER trg_task7_deliverable_manifest_references_append_only ON public.deliverable_manifest_references",
    "DROP TRIGGER trg_task7_data_room_packages_source_bound ON public.data_room_packages",
    "DROP TRIGGER trg_task7_data_room_package_files_source_bound ON public.data_room_package_files",
    "DROP TRIGGER trg_task7_document_artifacts_source_bound ON public.document_artifacts",
    "DROP TRIGGER trg_task7_documents_source_bound ON public.documents",
    "DROP TRIGGER trg_task7_document_parse_attempts_source_bound ON public.document_parse_attempts",
    "DROP TRIGGER trg_task7_document_spans_source_bound ON public.document_spans",
    "DROP TRIGGER trg_task7_methodology_coverage_records_output_bound ON public.methodology_coverage_records",
    "DROP TRIGGER trg_task7_methodology_extraction_tasks_output_bound ON public.methodology_extraction_tasks",
    "DROP TRIGGER trg_task7_methodology_extraction_results_output_bound ON public.methodology_extraction_results",
    "DROP TRIGGER trg_task7_methodology_result_factual_outputs_output_bound ON public.methodology_result_factual_outputs",
    "DROP TRIGGER trg_task7_methodology_result_claim_links_output_bound ON public.methodology_result_claim_links",
    "DROP TRIGGER trg_task7_methodology_result_evidence_links_output_bound ON public.methodology_result_evidence_links",
    "DROP TRIGGER trg_task7_methodology_result_sanad_links_output_bound ON public.methodology_result_sanad_links",
    "DROP TRIGGER trg_task7_methodology_result_defect_links_output_bound ON public.methodology_result_defect_links",
    "DROP TRIGGER trg_task7_deterministic_calculations_output_bound ON public.deterministic_calculations",
    "DROP TRIGGER trg_task7_vector_embeddings_output_bound ON public.vector_embeddings",
    "DROP TRIGGER trg_task7_evidence_trust_findings_output_bound ON public.evidence_trust_findings",
    "DROP TRIGGER trg_task7_muhasabah_records_output_bound ON public.muhasabah_records",
    "DROP TRIGGER trg_task7_validated_evidence_packages_output_bound ON public.validated_evidence_packages",
    "DROP TRIGGER trg_task7_debate_sessions_output_bound ON public.debate_sessions",
    "DROP TRIGGER trg_task7_layer2_ic_challenges_output_bound ON public.layer2_ic_challenges",
    "DROP TRIGGER trg_task7_layer2_ic_findings_output_bound ON public.layer2_ic_findings",
    "DROP TRIGGER trg_task7_deliverables_output_bound ON public.deliverables",
    "DROP TRIGGER trg_task7_runs_contract_immutable ON public.runs",
    "DROP TRIGGER trg_task7_run_steps_contract_immutable ON public.run_steps",
    "DROP TRIGGER trg_methodology_coverage_scope ON public.methodology_coverage_records",
    "DROP TRIGGER trg_methodology_extraction_tasks_immutable ON public.methodology_extraction_tasks",
    "DROP TRIGGER trg_methodology_extraction_task_spans_immutable ON public.methodology_extraction_task_spans",
    "DROP TRIGGER trg_methodology_extraction_results_immutable ON public.methodology_extraction_results",
    "DROP TRIGGER trg_methodology_result_factual_outputs_immutable ON public.methodology_result_factual_outputs",
    "DROP TRIGGER trg_methodology_result_claim_links_immutable ON public.methodology_result_claim_links",
    "DROP TRIGGER trg_methodology_result_evidence_links_immutable ON public.methodology_result_evidence_links",
    "DROP TRIGGER trg_methodology_result_sanad_links_immutable ON public.methodology_result_sanad_links",
    "DROP TRIGGER trg_methodology_result_defect_links_immutable ON public.methodology_result_defect_links",
    "REVOKE EXECUTE ON FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (content_sha256,corpus_receipt_id,corpus_receipt_sha256,deal_id,object_identity_sha256,object_kind,source_disposition,source_lineage_object_id,source_version,tenant_id,write_origin) ON TABLE public.source_lineage_objects FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,metadata,package_id,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,status,tenant_id) ON TABLE public.data_room_packages FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,doc_id,document_id,error_codes,extension,file_entry_id,file_status,package_id,parse_status,path_hash,reason_codes,sequence,sha256,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,storage_uri,support_status,tenant_id,triage_status) ON TABLE public.data_room_package_files FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,doc_id,doc_type,ingested_at,metadata,sha256,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,source_system,tenant_id,title,uri,version_id) ON TABLE public.document_artifacts FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,doc_id,doc_type,document_id,metadata,parse_status,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,tenant_id) ON TABLE public.documents FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (attempt_ordinal,deal_id,document_id,finished_at,input_sha256,parse_attempt_id,parser_name,parser_version,receipt_sha256,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,started_at,status,tenant_id,write_origin) ON TABLE public.document_parse_attempts FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (content_hash,deal_id,document_id,locator,parse_attempt_id,source_binding_state,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind,span_id,span_type,tenant_id) ON TABLE public.document_spans FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,native_identity_sha256,native_output_sha256,output_class,output_kind,output_object_id,pipeline_contract_hash,pipeline_contract_version,run_id,tenant_id) ON TABLE public.run_output_objects FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (coverage_record_id,deal_id,methodology_question_id,methodology_question_record_id,methodology_type,methodology_version_id,methodology_version_record_id,run_id,run_output_object_id,run_output_object_kind,selection_id,tenant_id) ON TABLE public.methodology_coverage_records FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (candidate_span_set_sha256,coverage_record_id,deal_id,document_classification_id,document_classification_sha256,document_id,extraction_task_id,methodology_question_id,methodology_question_record_id,methodology_type,methodology_version_id,methodology_version_record_id,planner_contract_version,run_id,run_output_object_id,run_output_object_kind,selection_id,task_contract_version,task_input_sha256,task_record_id,tenant_id) ON TABLE public.methodology_extraction_tasks FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (attempt_id,deal_id,gap_finding,outcome,provider_provenance_sha256,result_id,result_sha256,run_id,run_output_object_id,run_output_object_kind,searched_document_ids,searched_scope_sha256,searched_span_ids,task_input_sha256,task_record_id,tenant_id) ON TABLE public.methodology_extraction_results FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (accepted_locator_set_sha256,accepted_locator_sha256s,accepted_source_span_ids,deal_id,factual_output_id,factual_output_record_id,output_ordinal,result_id,run_id,run_output_object_id,run_output_object_kind,task_record_id,tenant_id,typed_output_sha256) ON TABLE public.methodology_result_factual_outputs FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (canonical_key_sha256,claim_id,claim_link_id,coverage_record_id,deal_id,factual_output_record_id,methodology_question_record_id,methodology_version_record_id,result_id,reused_existing_claim,run_id,run_output_object_id,run_output_object_kind,selection_id,tenant_id) ON TABLE public.methodology_result_claim_links FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (claim_link_id,deal_id,document_id,evidence_id,evidence_link_id,factual_output_record_id,locator_sha256,result_id,run_id,run_output_object_id,run_output_object_kind,source_span_id,tenant_id) ON TABLE public.methodology_result_evidence_links FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (claim_link_id,deal_id,factual_output_record_id,primary_evidence_id,result_id,run_id,run_output_object_id,run_output_object_kind,sanad_id,sanad_link_id,tenant_id) ON TABLE public.methodology_result_sanad_links FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,defect_id,defect_link_id,factual_output_record_id,result_id,run_id,run_output_object_id,run_output_object_kind,sanad_link_id,tenant_id) ON TABLE public.methodology_result_defect_links FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (calc_id,calc_type,code_version,deal_id,formula_hash,inputs,output,reproducibility_hash,run_id,run_output_object_id,run_output_object_kind,tenant_id) ON TABLE public.deterministic_calculations FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (content_hash,deal_id,embedding,embedding_dimensions,embedding_id,embedding_model,run_id,run_output_object_id,run_output_object_kind,source_id,source_type,tenant_id) ON TABLE public.vector_embeddings FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (calc_ids,claim_id,court_id,deal_id,defect_ids,evidence_ids,finding_id,finding_type,reason_codes,run_id,run_output_object_id,run_output_object_kind,sanad_id,tenant_id) ON TABLE public.evidence_trust_findings FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (agent_id,confidence,deal_id,is_subjective,output_id,record_id,record_timestamp,run_id,run_output_object_id,run_output_object_kind,source_step,supported_calc_ids,supported_claim_ids,tenant_id,uncertainties) ON TABLE public.muhasabah_records FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (court_id,dashboard_id,deal_id,package_id,run_id,run_output_object_id,run_output_object_kind,safe_summary,status,tenant_id) ON TABLE public.validated_evidence_packages FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,debate_id,max_rounds,protocol_version,rounds,run_id,run_output_object_id,run_output_object_kind,status,tenant_id) ON TABLE public.debate_sessions FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (challenge_id,deal_id,run_id,run_output_object_id,run_output_object_kind,safe_summary,source_debate_id,status,tenant_id) ON TABLE public.layer2_ic_challenges FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (category,challenge_id,deal_id,enrichment_ref_ids,finding_id,finding_type,graph_ref_ids,rag_ref_ids,run_id,run_output_object_id,run_output_object_kind,severity,supported_calc_ids,supported_claim_ids,tenant_id) ON TABLE public.layer2_ic_findings FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (catalog_ordinal,content_sha256,content_type,deal_id,deliverable_id,deliverable_type,download_proof_hash,format,object_version_hash,run_id,run_output_object_id,run_output_object_kind,size_bytes,status,tenant_id,uri,uri_sha256) ON TABLE public.deliverables FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (calc_grade,calc_id,calc_sanad_id,deal_id,explanation,input_claim_ids,input_min_sanad_grade,run_id,tenant_id) ON TABLE public.calc_sanads FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (content_sha256,deal_id,document_id,locator_sha256,run_id,span_id,span_ordinal,task_record_id,task_span_record_id,tenant_id) ON TABLE public.methodology_extraction_task_spans FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,lease_expires_at,lease_fencing_token,lease_owner_id,pipeline_contract_hash,pipeline_contract_version,pipeline_resume_state,run_id,status,strict_preflight_proof_id,tenant_id) ON TABLE public.runs FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (run_id,status,step_id,step_name,step_order,tenant_id) ON TABLE public.run_steps FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (deal_id,output_kind,output_object_id,output_ordinal,run_id,semantic_rule_hash,semantic_rule_version,step_id,step_name,subrole,tenant_id) ON TABLE public.run_step_outputs FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (consumer_step_name,consumer_subrole,deal_id,expected_edge_id,run_id,tenant_id) ON TABLE public.run_expected_consumer_edges FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (consumer_output_object_id,consumer_step_name,consumer_subrole,deal_id,expected_edge_id,run_id,tenant_id) ON TABLE public.run_consumer_receipts FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (catalog_ordinal,deal_id,manifest_deliverable_id,manifest_output_object_id,member_content_sha256,member_identity_sha256,member_kind,member_output_object_id,member_step_output_id,member_value_sha256,reference_sha256,run_id,tenant_id) ON TABLE public.deliverable_manifest_references FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (consumed_by_run_id,corpus_hash,deal_id,pipeline_contract_hash,pipeline_contract_version,proof_id,tenant_id) ON TABLE public.strict_preflight_proofs FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (commercial_dd_version_record_id,deal_id,financial_dd_version_record_id,run_id,selection_id,tenant_id) ON TABLE public.run_methodology_selections FROM idis_task7_guard_function_owner",
    "REVOKE SELECT (methodology_question_id,methodology_question_record_id,methodology_type,methodology_version_id,methodology_version_record_id,tenant_id) ON TABLE public.methodology_questions FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.source_lineage_objects FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.data_room_packages FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.data_room_package_files FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_artifacts FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.documents FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_parse_attempts FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_spans FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_output_objects FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_coverage_records FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_tasks FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_results FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_factual_outputs FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_claim_links FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_evidence_links FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_sanad_links FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_defect_links FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deterministic_calculations FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.vector_embeddings FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.evidence_trust_findings FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.muhasabah_records FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.validated_evidence_packages FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.debate_sessions FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.layer2_ic_challenges FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.layer2_ic_findings FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deliverables FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.calc_sanads FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_task_spans FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.runs FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_steps FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_step_outputs FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_expected_consumer_edges FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_consumer_receipts FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deliverable_manifest_references FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.strict_preflight_proofs FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_methodology_selections FROM idis_task7_guard_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_questions FROM idis_task7_guard_function_owner",
    "REVOKE USAGE ON SCHEMA public FROM idis_task7_guard_function_owner",
    "DROP FUNCTION public.idis_task7_current_source_exact_one_v1()",
    "DROP FUNCTION public.idis_task7_current_output_exact_one_v1()",
    "DROP FUNCTION public.idis_task7_source_projection_v1(uuid)",
    "DROP FUNCTION public.idis_task7_current_native_hashes_v1(uuid,uuid,uuid,uuid)",
    "DROP FUNCTION public.idis_task6_immutable_guard()",
    "DROP FUNCTION public.idis_task7_source_hash_jsonb_v1(jsonb)",
    "DROP FUNCTION public.idis_task7_uuidv5_v1(uuid,text)",
    "DROP FUNCTION public.idis_task7_hash_jsonb_v1(jsonb)",
    "DROP FUNCTION public.idis_task7_current_receipt_equality_v1()",
    "DROP FUNCTION public.idis_task7_current_append_only_lease_v1()",
    "DROP FUNCTION public.idis_task7_current_source_bound_v1()",
    "DROP FUNCTION public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid)",
    "DROP FUNCTION public.idis_task7_source_canonical_jsonb_text_v1(jsonb)",
    "DROP FUNCTION public.idis_task7_sha1_v1(bytea)",
    "DROP FUNCTION public.idis_task7_canonical_jsonb_text_v1(jsonb)",
    "DROP FUNCTION public.idis_task7_manifest_reference_digest_v1(uuid,uuid,uuid,text,text,uuid,text,integer,uuid,uuid,text,text,text)",
    "DROP FUNCTION public.idis_task7_current_input_binding_admission_v1()",
    "DROP FUNCTION public.idis_task7_expected_edge_admission_is_valid_v1(jsonb,uuid,uuid,uuid)",
    "DROP FUNCTION public.idis_task7_package_source_transition_is_valid_v1(jsonb,jsonb)",
    "DROP FUNCTION public.idis_task7_current_output_bound_v1()",
    "DROP FUNCTION public.idis_task7_current_runs_contract_immutable_v1()",
    "DROP FUNCTION public.idis_task7_current_run_steps_contract_immutable_v1()",
    "DROP FUNCTION public.idis_task5_coverage_scope_guard()",
    "DROP FUNCTION public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)",
    "DO $$DECLARE guard_oid oid; BEGIN SELECT oid INTO guard_oid FROM pg_catalog.pg_roles WHERE rolname='idis_task7_guard_function_owner'; IF guard_oid IS NULL OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=guard_oid OR m.roleid=guard_oid) OR EXISTS (SELECT 1 FROM pg_catalog.pg_database d WHERE d.datdba=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(d.datacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspowner=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(n.nspacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.relowner=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(c.relacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute at WHERE at.attnum>0 AND NOT at.attisdropped AND EXISTS (SELECT 1 FROM pg_catalog.aclexplode(at.attacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p WHERE p.proowner=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a WHERE a.grantee=guard_oid)) OR EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl d WHERE d.defaclrole=guard_oid OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(d.defaclacl) a WHERE a.grantee=guard_oid)) THEN RAISE EXCEPTION 'TASK7_GUARD_FUNCTION_OWNER_DOWNGRADE_ACL_INVALID' USING ERRCODE='42501'; END IF; END$$",
    "CREATE OR REPLACE FUNCTION public.idis_prevent_strict_preflight_proof_mutation()\n RETURNS trigger\n LANGUAGE plpgsql\nAS $function$\n        BEGIN\n            IF TG_OP = 'INSERT' THEN\n                IF NEW.consumed_at IS NULL\n                   AND NEW.consumed_by_run_id IS NULL\n                THEN\n                    -- Hold the parent row through proof-insert commit.  Without this\n                    -- lock, a concurrent deal erasure could commit between a plain\n                    -- existence check and the proof insert, creating an orphan.\n                    PERFORM 1\n                    FROM deals\n                    WHERE deals.tenant_id = NEW.tenant_id\n                      AND deals.deal_id = NEW.deal_id\n                    FOR KEY SHARE;\n                    IF FOUND THEN\n                        RETURN NEW;\n                    END IF;\n                END IF;\n\n                RAISE EXCEPTION\n                    'Proof insert requires unconsumed state and a live same-tenant deal';\n            ELSIF TG_OP = 'UPDATE' THEN\n                IF NEW.proof_id IS NOT DISTINCT FROM OLD.proof_id\n                   AND NEW.tenant_id IS NOT DISTINCT FROM OLD.tenant_id\n                   AND NEW.deal_id IS NOT DISTINCT FROM OLD.deal_id\n                   AND NEW.corpus_hash IS NOT DISTINCT FROM OLD.corpus_hash\n                   AND NEW.strict_profile_generation\n                       IS NOT DISTINCT FROM OLD.strict_profile_generation\n                   AND NEW.strict_profile_hmac IS NOT DISTINCT FROM OLD.strict_profile_hmac\n                   AND NEW.pipeline_contract_version\n                       IS NOT DISTINCT FROM OLD.pipeline_contract_version\n                   AND NEW.pipeline_contract_hash\n                       IS NOT DISTINCT FROM OLD.pipeline_contract_hash\n                   AND NEW.expected_consumer_edge_rule_hash\n                       IS NOT DISTINCT FROM OLD.expected_consumer_edge_rule_hash\n                   AND NEW.static_decision_set_hash\n                       IS NOT DISTINCT FROM OLD.static_decision_set_hash\n                   AND NEW.methodology_version_hashes\n                       IS NOT DISTINCT FROM OLD.methodology_version_hashes\n                   AND NEW.promoted_pointer_hash IS NOT DISTINCT FROM OLD.promoted_pointer_hash\n                   AND NEW.provider_receipt_set_hash\n                       IS NOT DISTINCT FROM OLD.provider_receipt_set_hash\n                   AND NEW.dependency_receipt_set_hash\n                       IS NOT DISTINCT FROM OLD.dependency_receipt_set_hash\n                   AND NEW.binding_hash IS NOT DISTINCT FROM OLD.binding_hash\n                   AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at\n                   AND NEW.expires_at IS NOT DISTINCT FROM OLD.expires_at\n                   AND OLD.consumed_at IS NULL\n                   AND OLD.consumed_by_run_id IS NULL\n                   AND OLD.expires_at > statement_timestamp()\n                   AND NEW.consumed_at IS NOT NULL\n                   AND NEW.consumed_at <= OLD.expires_at\n                   AND NEW.consumed_by_run_id IS NOT NULL\n                   AND EXISTS (\n                       SELECT 1\n                       FROM runs\n                       WHERE runs.run_id = NEW.consumed_by_run_id\n                         AND runs.tenant_id = NEW.tenant_id\n                         AND runs.deal_id = NEW.deal_id\n                         AND runs.strict_preflight_proof_id = NEW.proof_id\n                   )\n                THEN\n                    RETURN NEW;\n                END IF;\n\n                RAISE EXCEPTION\n                    'Strict proof immutable; only first reciprocal consumption is allowed';\n            ELSIF TG_OP = 'DELETE' THEN\n                IF NOT EXISTS (\n                    SELECT 1\n                    FROM deals\n                    WHERE deals.tenant_id = OLD.tenant_id\n                      AND deals.deal_id = OLD.deal_id\n                )\n                THEN\n                    RETURN OLD;\n                END IF;\n\n                RAISE EXCEPTION\n                    'Strict preflight proof cannot be deleted while its same-tenant deal exists';\n            END IF;\n\n            RAISE EXCEPTION\n                'Unsupported strict preflight proof mutation';\n        END;\n        $function$\n",
    "\n        CREATE OR REPLACE FUNCTION idis_task5_selection_guard()\n        RETURNS trigger LANGUAGE plpgsql AS $$\n        DECLARE\n            commercial_count integer;\n            financial_count integer;\n        BEGIN\n            IF TG_OP = 'DELETE' AND EXISTS (\n                SELECT 1 FROM erasure_requests e\n                WHERE e.tenant_id = OLD.tenant_id\n                  AND e.deal_id = OLD.deal_id\n                  AND e.status = 'REQUESTED'\n            ) THEN\n                RETURN OLD;\n            END IF;\n            IF TG_OP <> 'INSERT' THEN\n                RAISE EXCEPTION 'RUN_METHODOLOGY_SELECTION_IMMUTABLE'\n                    USING ERRCODE = '23514';\n            END IF;\n            SELECT question_count INTO commercial_count\n            FROM methodology_versions\n            WHERE tenant_id = NEW.tenant_id\n              AND methodology_version_record_id = NEW.commercial_dd_version_record_id\n              AND methodology_version_id = NEW.commercial_methodology_version_id\n              AND version_hash = NEW.commercial_version_hash\n              AND methodology_type = 'commercial_dd'\n              AND status = 'FROZEN';\n            SELECT question_count INTO financial_count\n            FROM methodology_versions\n            WHERE tenant_id = NEW.tenant_id\n              AND methodology_version_record_id = NEW.financial_dd_version_record_id\n              AND methodology_version_id = NEW.financial_methodology_version_id\n              AND version_hash = NEW.financial_version_hash\n              AND methodology_type = 'financial_dd'\n              AND status = 'FROZEN';\n            IF commercial_count IS DISTINCT FROM 21\n               OR financial_count IS DISTINCT FROM 79\n               OR commercial_count + financial_count <> NEW.selected_question_count THEN\n                RAISE EXCEPTION 'RUN_METHODOLOGY_SELECTION_INVALID'\n                    USING ERRCODE = '23514';\n            END IF;\n            RETURN NEW;\n        END\n        $$\n        ",
    "\n        CREATE OR REPLACE FUNCTION idis_task5_coverage_scope_guard()\n        RETURNS trigger LANGUAGE plpgsql AS $$\n        BEGIN\n            IF TG_OP = 'DELETE' THEN\n                RETURN OLD;\n            END IF;\n            IF TG_OP = 'UPDATE' AND (\n                NEW.coverage_record_id IS DISTINCT FROM OLD.coverage_record_id\n                OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id\n                OR NEW.deal_id IS DISTINCT FROM OLD.deal_id\n                OR NEW.run_id IS DISTINCT FROM OLD.run_id\n                OR NEW.selection_id IS DISTINCT FROM OLD.selection_id\n                OR NEW.methodology_version_record_id IS DISTINCT FROM\n                   OLD.methodology_version_record_id\n                OR NEW.methodology_version_id IS DISTINCT FROM OLD.methodology_version_id\n                OR NEW.methodology_question_record_id IS DISTINCT FROM\n                   OLD.methodology_question_record_id\n                OR NEW.methodology_question_id IS DISTINCT FROM OLD.methodology_question_id\n                OR NEW.methodology_type IS DISTINCT FROM OLD.methodology_type\n                OR NEW.created_at IS DISTINCT FROM OLD.created_at\n            ) THEN\n                RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE'\n                    USING ERRCODE = '23514';\n            END IF;\n            IF NOT EXISTS (\n                SELECT 1\n                FROM run_methodology_selections s\n                JOIN methodology_questions q\n                  ON q.tenant_id = NEW.tenant_id\n                 AND q.methodology_version_record_id = NEW.methodology_version_record_id\n                 AND q.methodology_version_id = NEW.methodology_version_id\n                 AND q.methodology_question_record_id = NEW.methodology_question_record_id\n                 AND q.methodology_question_id = NEW.methodology_question_id\n                 AND q.methodology_type = NEW.methodology_type\n                WHERE s.tenant_id = NEW.tenant_id\n                  AND s.deal_id = NEW.deal_id\n                  AND s.run_id = NEW.run_id\n                  AND s.selection_id = NEW.selection_id\n                  AND (\n                    (q.methodology_type = 'commercial_dd'\n                     AND q.methodology_version_record_id = s.commercial_dd_version_record_id)\n                    OR\n                    (q.methodology_type = 'financial_dd'\n                     AND q.methodology_version_record_id = s.financial_dd_version_record_id)\n                  )\n            ) THEN\n                RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_INVALID'\n                    USING ERRCODE = '23514';\n            END IF;\n            NEW.updated_at := now();\n            RETURN NEW;\n        END\n        $$\n        ",
    "\n        CREATE OR REPLACE FUNCTION idis_task6_erasure_requested(\n            checked_tenant UUID, checked_deal UUID\n        ) RETURNS boolean LANGUAGE sql STABLE AS $$\n            SELECT EXISTS (\n                SELECT 1 FROM erasure_requests e\n                WHERE e.tenant_id = checked_tenant\n                  AND e.deal_id = checked_deal\n                  AND e.status = 'REQUESTED'\n            )\n        $$\n        ",
    "\n        CREATE OR REPLACE FUNCTION idis_task6_current_lease_is_valid(\n            checked_tenant UUID, checked_deal UUID, checked_run UUID\n        ) RETURNS boolean LANGUAGE plpgsql STABLE AS $$\n        DECLARE\n            current_tenant_id UUID;\n            current_deal_id UUID;\n            current_run_id UUID;\n            current_owner_id UUID;\n            current_fencing_token BIGINT;\n        BEGIN\n            BEGIN\n                current_tenant_id := NULLIF(\n                    current_setting('idis.worker_lease_tenant_id', true), ''\n                )::uuid;\n                current_deal_id := NULLIF(\n                    current_setting('idis.worker_lease_deal_id', true), ''\n                )::uuid;\n                current_run_id := NULLIF(\n                    current_setting('idis.worker_lease_run_id', true), ''\n                )::uuid;\n                current_owner_id := NULLIF(\n                    current_setting('idis.worker_lease_owner_id', true), ''\n                )::uuid;\n                current_fencing_token := NULLIF(\n                    current_setting('idis.worker_lease_fencing_token', true), ''\n                )::bigint;\n            EXCEPTION WHEN OTHERS THEN\n                RETURN false;\n            END;\n            RETURN EXISTS (\n                SELECT 1 FROM runs r\n                WHERE r.tenant_id = checked_tenant\n                  AND r.deal_id = checked_deal\n                  AND r.run_id = checked_run\n                  AND r.status = 'RUNNING'\n                  AND r.tenant_id = current_tenant_id\n                  AND r.deal_id = current_deal_id\n                  AND r.run_id = current_run_id\n                  AND r.lease_owner_id = current_owner_id\n                  AND r.lease_fencing_token = current_fencing_token\n                  AND r.lease_expires_at > statement_timestamp()\n            );\n        END\n        $$\n        ",
    "\n        CREATE OR REPLACE FUNCTION idis_task6_immutable_guard()\n        RETURNS trigger LANGUAGE plpgsql AS $$\n        BEGIN\n            IF TG_OP = 'INSERT' THEN\n                IF NOT idis_task6_current_lease_is_valid(\n                    NEW.tenant_id, NEW.deal_id, NEW.run_id\n                ) THEN\n                    RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';\n                END IF;\n                RETURN NEW;\n            END IF;\n            IF TG_OP = 'DELETE'\n               AND idis_task6_erasure_requested(OLD.tenant_id, OLD.deal_id) THEN\n                RETURN OLD;\n            END IF;\n            RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE'\n                USING ERRCODE = '23514';\n        END\n        $$\n        ",
    "\n        CREATE OR REPLACE FUNCTION idis_task6_attempt_guard()\n        RETURNS trigger LANGUAGE plpgsql AS $$\n        DECLARE\n            current_tenant_id UUID;\n            current_deal_id UUID;\n            current_run_id UUID;\n            current_owner_id UUID;\n            current_fencing_token BIGINT;\n        BEGIN\n            IF TG_OP = 'DELETE' THEN\n                IF idis_task6_erasure_requested(OLD.tenant_id, OLD.deal_id) THEN\n                    RETURN OLD;\n                END IF;\n                RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE'\n                    USING ERRCODE = '23514';\n            END IF;\n            BEGIN\n                current_tenant_id := NULLIF(\n                    current_setting('idis.worker_lease_tenant_id', true), ''\n                )::uuid;\n                current_deal_id := NULLIF(\n                    current_setting('idis.worker_lease_deal_id', true), ''\n                )::uuid;\n                current_run_id := NULLIF(\n                    current_setting('idis.worker_lease_run_id', true), ''\n                )::uuid;\n                current_owner_id := NULLIF(\n                    current_setting('idis.worker_lease_owner_id', true), ''\n                )::uuid;\n                current_fencing_token := NULLIF(\n                    current_setting('idis.worker_lease_fencing_token', true), ''\n                )::bigint;\n            EXCEPTION WHEN OTHERS THEN\n                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';\n            END;\n            IF NOT EXISTS (\n                SELECT 1 FROM runs r\n                WHERE r.tenant_id = NEW.tenant_id\n                  AND r.deal_id = NEW.deal_id\n                  AND r.run_id = NEW.run_id\n                  AND r.status = 'RUNNING'\n                  AND r.tenant_id = current_tenant_id\n                  AND r.deal_id = current_deal_id\n                  AND r.run_id = current_run_id\n                  AND r.lease_owner_id = current_owner_id\n                  AND r.lease_fencing_token = current_fencing_token\n                  AND r.lease_expires_at > statement_timestamp()\n            ) THEN\n                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';\n            END IF;\n            IF NOT EXISTS (\n                SELECT 1 FROM run_steps s\n                WHERE s.tenant_id = NEW.tenant_id\n                  AND s.run_id = NEW.run_id\n                  AND s.step_id = NEW.run_step_id\n                  AND s.step_name = 'METHODOLOGY_EXTRACTION_TASK_EXECUTION'\n                  AND s.step_order = 6\n                  AND s.status = 'RUNNING'\n            ) THEN\n                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';\n            END IF;\n            IF TG_OP = 'INSERT' THEN\n                IF NEW.status <> 'STARTED' THEN\n                    RAISE EXCEPTION 'METHODOLOGY_ATTEMPT_MUST_START'\n                        USING ERRCODE = '23514';\n                END IF;\n                IF NEW.lease_owner_id <> current_owner_id\n                   OR NEW.lease_fencing_token <> current_fencing_token THEN\n                    RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';\n                END IF;\n                RETURN NEW;\n            END IF;\n            IF OLD.status <> 'STARTED'\n               OR NEW.status NOT IN ('SUCCEEDED', 'FAILED', 'OUTCOME_UNKNOWN')\n               OR (to_jsonb(NEW) - ARRAY[\n                    'status', 'observed_model', 'provider_request_id',\n                    'provider_response_sha256', 'dispatched_call_sha256',\n                    'completed_at', 'latency_ms',\n                    'input_tokens', 'output_tokens', 'stop_reason',\n                    'budget_terminal_state', 'receipt_sha256', 'safe_failure_code'\n                  ]::text[])\n                  IS DISTINCT FROM\n                  (to_jsonb(OLD) - ARRAY[\n                    'status', 'observed_model', 'provider_request_id',\n                    'provider_response_sha256', 'dispatched_call_sha256',\n                    'completed_at', 'latency_ms',\n                    'input_tokens', 'output_tokens', 'stop_reason',\n                    'budget_terminal_state', 'receipt_sha256', 'safe_failure_code'\n                  ]::text[]) THEN\n                RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE'\n                    USING ERRCODE = '23514';\n            END IF;\n            IF NEW.status IN ('SUCCEEDED', 'FAILED')\n               AND (\n                    current_owner_id <> OLD.lease_owner_id\n                    OR current_fencing_token <> OLD.lease_fencing_token\n               ) THEN\n                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';\n            END IF;\n            IF NEW.status = 'OUTCOME_UNKNOWN'\n               AND NOT (\n                    (\n                        current_owner_id = OLD.lease_owner_id\n                        AND current_fencing_token = OLD.lease_fencing_token\n                    )\n                    OR current_fencing_token > OLD.lease_fencing_token\n               ) THEN\n                RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001';\n            END IF;\n            RETURN NEW;\n        END\n        $$\n        ",
    "\n        CREATE OR REPLACE FUNCTION idis_task6_validate_result_outputs()\n        RETURNS trigger LANGUAGE plpgsql AS $$\n        DECLARE\n            checked_result UUID;\n            checked_outcome TEXT;\n            output_count BIGINT;\n        BEGIN\n            checked_result := COALESCE(NEW.result_id, OLD.result_id);\n            SELECT outcome INTO checked_outcome\n            FROM methodology_extraction_results\n            WHERE result_id = checked_result;\n            IF checked_outcome IS NULL THEN\n                RETURN NULL;\n            END IF;\n            SELECT count(*) INTO output_count\n            FROM methodology_result_factual_outputs\n            WHERE result_id = checked_result;\n            IF (checked_outcome = 'EVIDENCE_FOUND' AND output_count = 0)\n               OR (checked_outcome = 'EVIDENCE_NOT_FOUND' AND output_count <> 0) THEN\n                RAISE EXCEPTION 'METHODOLOGY_RESULT_FACTUAL_OUTPUT_MISMATCH'\n                    USING ERRCODE = '23514';\n            END IF;\n            RETURN NULL;\n        END\n        $$\n        ",
    "CREATE TRIGGER trg_methodology_coverage_scope BEFORE INSERT OR UPDATE OR DELETE ON public.methodology_coverage_records FOR EACH ROW EXECUTE FUNCTION public.idis_task5_coverage_scope_guard()",
    "CREATE TRIGGER trg_methodology_extraction_tasks_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.methodology_extraction_tasks FOR EACH ROW EXECUTE FUNCTION public.idis_task6_immutable_guard()",
    "CREATE TRIGGER trg_methodology_extraction_task_spans_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.methodology_extraction_task_spans FOR EACH ROW EXECUTE FUNCTION public.idis_task6_immutable_guard()",
    "CREATE TRIGGER trg_methodology_extraction_results_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.methodology_extraction_results FOR EACH ROW EXECUTE FUNCTION public.idis_task6_immutable_guard()",
    "CREATE TRIGGER trg_methodology_result_factual_outputs_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.methodology_result_factual_outputs FOR EACH ROW EXECUTE FUNCTION public.idis_task6_immutable_guard()",
    "CREATE TRIGGER trg_methodology_result_claim_links_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.methodology_result_claim_links FOR EACH ROW EXECUTE FUNCTION public.idis_task6_immutable_guard()",
    "CREATE TRIGGER trg_methodology_result_evidence_links_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.methodology_result_evidence_links FOR EACH ROW EXECUTE FUNCTION public.idis_task6_immutable_guard()",
    "CREATE TRIGGER trg_methodology_result_sanad_links_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.methodology_result_sanad_links FOR EACH ROW EXECUTE FUNCTION public.idis_task6_immutable_guard()",
    "CREATE TRIGGER trg_methodology_result_defect_links_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.methodology_result_defect_links FOR EACH ROW EXECUTE FUNCTION public.idis_task6_immutable_guard()",
    "DO $$BEGIN IF EXISTS (SELECT 1 FROM (VALUES ('public.idis_prevent_strict_preflight_proof_mutation()','0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d2027494e5345525427205448454e0a202020202020202020202020202020204946204e45572e636f6e73756d65645f6174204953204e554c4c0a20202020202020202020202020202020202020414e44204e45572e636f6e73756d65645f62795f72756e5f6964204953204e554c4c0a202020202020202020202020202020205448454e0a20202020202020202020202020202020202020202d2d20486f6c642074686520706172656e7420726f77207468726f7567682070726f6f662d696e7365727420636f6d6d69742e2020576974686f757420746869730a20202020202020202020202020202020202020202d2d206c6f636b2c206120636f6e63757272656e74206465616c206572617375726520636f756c6420636f6d6d6974206265747765656e206120706c61696e0a20202020202020202020202020202020202020202d2d206578697374656e636520636865636b20616e64207468652070726f6f6620696e736572742c206372656174696e6720616e206f727068616e2e0a2020202020202020202020202020202020202020504552464f524d20310a202020202020202020202020202020202020202046524f4d206465616c730a20202020202020202020202020202020202020205748455245206465616c732e74656e616e745f6964203d204e45572e74656e616e745f69640a20202020202020202020202020202020202020202020414e44206465616c732e6465616c5f6964203d204e45572e6465616c5f69640a2020202020202020202020202020202020202020464f52204b45592053484152453b0a2020202020202020202020202020202020202020494620464f554e44205448454e0a20202020202020202020202020202020202020202020202052455455524e204e45573b0a2020202020202020202020202020202020202020454e442049463b0a20202020202020202020202020202020454e442049463b0a0a20202020202020202020202020202020524149534520455843455054494f4e0a20202020202020202020202020202020202020202750726f6f6620696e7365727420726571756972657320756e636f6e73756d656420737461746520616e642061206c6976652073616d652d74656e616e74206465616c273b0a202020202020202020202020454c5349462054475f4f50203d202755504441544527205448454e0a202020202020202020202020202020204946204e45572e70726f6f665f6964204953204e4f542044495354494e43542046524f4d204f4c442e70726f6f665f69640a20202020202020202020202020202020202020414e44204e45572e74656e616e745f6964204953204e4f542044495354494e43542046524f4d204f4c442e74656e616e745f69640a20202020202020202020202020202020202020414e44204e45572e6465616c5f6964204953204e4f542044495354494e43542046524f4d204f4c442e6465616c5f69640a20202020202020202020202020202020202020414e44204e45572e636f727075735f68617368204953204e4f542044495354494e43542046524f4d204f4c442e636f727075735f686173680a20202020202020202020202020202020202020414e44204e45572e7374726963745f70726f66696c655f67656e65726174696f6e0a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e7374726963745f70726f66696c655f67656e65726174696f6e0a20202020202020202020202020202020202020414e44204e45572e7374726963745f70726f66696c655f686d6163204953204e4f542044495354494e43542046524f4d204f4c442e7374726963745f70726f66696c655f686d61630a20202020202020202020202020202020202020414e44204e45572e706970656c696e655f636f6e74726163745f76657273696f6e0a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e706970656c696e655f636f6e74726163745f76657273696f6e0a20202020202020202020202020202020202020414e44204e45572e706970656c696e655f636f6e74726163745f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e706970656c696e655f636f6e74726163745f686173680a20202020202020202020202020202020202020414e44204e45572e65787065637465645f636f6e73756d65725f656467655f72756c655f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e65787065637465645f636f6e73756d65725f656467655f72756c655f686173680a20202020202020202020202020202020202020414e44204e45572e7374617469635f6465636973696f6e5f7365745f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e7374617469635f6465636973696f6e5f7365745f686173680a20202020202020202020202020202020202020414e44204e45572e6d6574686f646f6c6f67795f76657273696f6e5f6861736865730a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e6d6574686f646f6c6f67795f76657273696f6e5f6861736865730a20202020202020202020202020202020202020414e44204e45572e70726f6d6f7465645f706f696e7465725f68617368204953204e4f542044495354494e43542046524f4d204f4c442e70726f6d6f7465645f706f696e7465725f686173680a20202020202020202020202020202020202020414e44204e45572e70726f76696465725f726563656970745f7365745f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e70726f76696465725f726563656970745f7365745f686173680a20202020202020202020202020202020202020414e44204e45572e646570656e64656e63795f726563656970745f7365745f686173680a20202020202020202020202020202020202020202020204953204e4f542044495354494e43542046524f4d204f4c442e646570656e64656e63795f726563656970745f7365745f686173680a20202020202020202020202020202020202020414e44204e45572e62696e64696e675f68617368204953204e4f542044495354494e43542046524f4d204f4c442e62696e64696e675f686173680a20202020202020202020202020202020202020414e44204e45572e637265617465645f6174204953204e4f542044495354494e43542046524f4d204f4c442e637265617465645f61740a20202020202020202020202020202020202020414e44204e45572e657870697265735f6174204953204e4f542044495354494e43542046524f4d204f4c442e657870697265735f61740a20202020202020202020202020202020202020414e44204f4c442e636f6e73756d65645f6174204953204e554c4c0a20202020202020202020202020202020202020414e44204f4c442e636f6e73756d65645f62795f72756e5f6964204953204e554c4c0a20202020202020202020202020202020202020414e44204f4c442e657870697265735f6174203e2073746174656d656e745f74696d657374616d7028290a20202020202020202020202020202020202020414e44204e45572e636f6e73756d65645f6174204953204e4f54204e554c4c0a20202020202020202020202020202020202020414e44204e45572e636f6e73756d65645f6174203c3d204f4c442e657870697265735f61740a20202020202020202020202020202020202020414e44204e45572e636f6e73756d65645f62795f72756e5f6964204953204e4f54204e554c4c0a20202020202020202020202020202020202020414e442045584953545320280a202020202020202020202020202020202020202020202053454c45435420310a202020202020202020202020202020202020202020202046524f4d2072756e730a202020202020202020202020202020202020202020202057484552452072756e732e72756e5f6964203d204e45572e636f6e73756d65645f62795f72756e5f69640a20202020202020202020202020202020202020202020202020414e442072756e732e74656e616e745f6964203d204e45572e74656e616e745f69640a20202020202020202020202020202020202020202020202020414e442072756e732e6465616c5f6964203d204e45572e6465616c5f69640a20202020202020202020202020202020202020202020202020414e442072756e732e7374726963745f707265666c696768745f70726f6f665f6964203d204e45572e70726f6f665f69640a20202020202020202020202020202020202020290a202020202020202020202020202020205448454e0a202020202020202020202020202020202020202052455455524e204e45573b0a20202020202020202020202020202020454e442049463b0a0a20202020202020202020202020202020524149534520455843455054494f4e0a2020202020202020202020202020202020202020275374726963742070726f6f6620696d6d757461626c653b206f6e6c79206669727374207265636970726f63616c20636f6e73756d7074696f6e20697320616c6c6f776564273b0a202020202020202020202020454c5349462054475f4f50203d202744454c45544527205448454e0a202020202020202020202020202020204946204e4f542045584953545320280a202020202020202020202020202020202020202053454c45435420310a202020202020202020202020202020202020202046524f4d206465616c730a20202020202020202020202020202020202020205748455245206465616c732e74656e616e745f6964203d204f4c442e74656e616e745f69640a20202020202020202020202020202020202020202020414e44206465616c732e6465616c5f6964203d204f4c442e6465616c5f69640a20202020202020202020202020202020290a202020202020202020202020202020205448454e0a202020202020202020202020202020202020202052455455524e204f4c443b0a20202020202020202020202020202020454e442049463b0a0a20202020202020202020202020202020524149534520455843455054494f4e0a20202020202020202020202020202020202020202753747269637420707265666c696768742070726f6f662063616e6e6f742062652064656c65746564207768696c65206974732073616d652d74656e616e74206465616c20657869737473273b0a202020202020202020202020454e442049463b0a0a202020202020202020202020524149534520455843455054494f4e0a2020202020202020202020202020202027556e737570706f727465642073747269637420707265666c696768742070726f6f66206d75746174696f6e273b0a2020202020202020454e443b0a2020202020202020'),('public.idis_task5_selection_guard()','0a20202020202020204445434c4152450a202020202020202020202020636f6d6d65726369616c5f636f756e7420696e74656765723b0a20202020202020202020202066696e616e6369616c5f636f756e7420696e74656765723b0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d202744454c4554452720414e442045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d20657261737572655f726571756573747320650a20202020202020202020202020202020574845524520652e74656e616e745f6964203d204f4c442e74656e616e745f69640a202020202020202020202020202020202020414e4420652e6465616c5f6964203d204f4c442e6465616c5f69640a202020202020202020202020202020202020414e4420652e737461747573203d2027524551554553544544270a20202020202020202020202029205448454e0a2020202020202020202020202020202052455455524e204f4c443b0a202020202020202020202020454e442049463b0a20202020202020202020202049462054475f4f50203c3e2027494e5345525427205448454e0a20202020202020202020202020202020524149534520455843455054494f4e202752554e5f4d4554484f444f4c4f47595f53454c454354494f4e5f494d4d555441424c45270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a20202020202020202020202053454c454354207175657374696f6e5f636f756e7420494e544f20636f6d6d65726369616c5f636f756e740a20202020202020202020202046524f4d206d6574686f646f6c6f67795f76657273696f6e730a20202020202020202020202057484552452074656e616e745f6964203d204e45572e74656e616e745f69640a2020202020202020202020202020414e44206d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d204e45572e636f6d6d65726369616c5f64645f76657273696f6e5f7265636f72645f69640a2020202020202020202020202020414e44206d6574686f646f6c6f67795f76657273696f6e5f6964203d204e45572e636f6d6d65726369616c5f6d6574686f646f6c6f67795f76657273696f6e5f69640a2020202020202020202020202020414e442076657273696f6e5f68617368203d204e45572e636f6d6d65726369616c5f76657273696f6e5f686173680a2020202020202020202020202020414e44206d6574686f646f6c6f67795f74797065203d2027636f6d6d65726369616c5f6464270a2020202020202020202020202020414e4420737461747573203d202746524f5a454e273b0a20202020202020202020202053454c454354207175657374696f6e5f636f756e7420494e544f2066696e616e6369616c5f636f756e740a20202020202020202020202046524f4d206d6574686f646f6c6f67795f76657273696f6e730a20202020202020202020202057484552452074656e616e745f6964203d204e45572e74656e616e745f69640a2020202020202020202020202020414e44206d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d204e45572e66696e616e6369616c5f64645f76657273696f6e5f7265636f72645f69640a2020202020202020202020202020414e44206d6574686f646f6c6f67795f76657273696f6e5f6964203d204e45572e66696e616e6369616c5f6d6574686f646f6c6f67795f76657273696f6e5f69640a2020202020202020202020202020414e442076657273696f6e5f68617368203d204e45572e66696e616e6369616c5f76657273696f6e5f686173680a2020202020202020202020202020414e44206d6574686f646f6c6f67795f74797065203d202766696e616e6369616c5f6464270a2020202020202020202020202020414e4420737461747573203d202746524f5a454e273b0a202020202020202020202020494620636f6d6d65726369616c5f636f756e742049532044495354494e43542046524f4d2032310a2020202020202020202020202020204f522066696e616e6369616c5f636f756e742049532044495354494e43542046524f4d2037390a2020202020202020202020202020204f5220636f6d6d65726369616c5f636f756e74202b2066696e616e6369616c5f636f756e74203c3e204e45572e73656c65637465645f7175657374696f6e5f636f756e74205448454e0a20202020202020202020202020202020524149534520455843455054494f4e202752554e5f4d4554484f444f4c4f47595f53454c454354494f4e5f494e56414c4944270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a20202020202020202020202052455455524e204e45573b0a2020202020202020454e440a2020202020202020'),('public.idis_task5_coverage_scope_guard()','0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d202744454c45544527205448454e0a2020202020202020202020202020202052455455524e204f4c443b0a202020202020202020202020454e442049463b0a20202020202020202020202049462054475f4f50203d20275550444154452720414e4420280a202020202020202020202020202020204e45572e636f7665726167655f7265636f72645f69642049532044495354494e43542046524f4d204f4c442e636f7665726167655f7265636f72645f69640a202020202020202020202020202020204f52204e45572e74656e616e745f69642049532044495354494e43542046524f4d204f4c442e74656e616e745f69640a202020202020202020202020202020204f52204e45572e6465616c5f69642049532044495354494e43542046524f4d204f4c442e6465616c5f69640a202020202020202020202020202020204f52204e45572e72756e5f69642049532044495354494e43542046524f4d204f4c442e72756e5f69640a202020202020202020202020202020204f52204e45572e73656c656374696f6e5f69642049532044495354494e43542046524f4d204f4c442e73656c656374696f6e5f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f69642049532044495354494e43542046524f4d0a202020202020202020202020202020202020204f4c442e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f76657273696f6e5f69642049532044495354494e43542046524f4d204f4c442e6d6574686f646f6c6f67795f76657273696f6e5f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f7175657374696f6e5f7265636f72645f69642049532044495354494e43542046524f4d0a202020202020202020202020202020202020204f4c442e6d6574686f646f6c6f67795f7175657374696f6e5f7265636f72645f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f7175657374696f6e5f69642049532044495354494e43542046524f4d204f4c442e6d6574686f646f6c6f67795f7175657374696f6e5f69640a202020202020202020202020202020204f52204e45572e6d6574686f646f6c6f67795f747970652049532044495354494e43542046524f4d204f4c442e6d6574686f646f6c6f67795f747970650a202020202020202020202020202020204f52204e45572e637265617465645f61742049532044495354494e43542046524f4d204f4c442e637265617465645f61740a20202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f434f5645524147455f53434f50455f494d4d555441424c45270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204e4f542045584953545320280a2020202020202020202020202020202053454c45435420310a2020202020202020202020202020202046524f4d2072756e5f6d6574686f646f6c6f67795f73656c656374696f6e7320730a202020202020202020202020202020204a4f494e206d6574686f646f6c6f67795f7175657374696f6e7320710a2020202020202020202020202020202020204f4e20712e74656e616e745f6964203d204e45572e74656e616e745f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d204e45572e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f76657273696f6e5f6964203d204e45572e6d6574686f646f6c6f67795f76657273696f6e5f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f7175657374696f6e5f7265636f72645f6964203d204e45572e6d6574686f646f6c6f67795f7175657374696f6e5f7265636f72645f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f7175657374696f6e5f6964203d204e45572e6d6574686f646f6c6f67795f7175657374696f6e5f69640a2020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f74797065203d204e45572e6d6574686f646f6c6f67795f747970650a20202020202020202020202020202020574845524520732e74656e616e745f6964203d204e45572e74656e616e745f69640a202020202020202020202020202020202020414e4420732e6465616c5f6964203d204e45572e6465616c5f69640a202020202020202020202020202020202020414e4420732e72756e5f6964203d204e45572e72756e5f69640a202020202020202020202020202020202020414e4420732e73656c656374696f6e5f6964203d204e45572e73656c656374696f6e5f69640a202020202020202020202020202020202020414e4420280a202020202020202020202020202020202020202028712e6d6574686f646f6c6f67795f74797065203d2027636f6d6d65726369616c5f6464270a202020202020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d20732e636f6d6d65726369616c5f64645f76657273696f6e5f7265636f72645f6964290a20202020202020202020202020202020202020204f520a202020202020202020202020202020202020202028712e6d6574686f646f6c6f67795f74797065203d202766696e616e6369616c5f6464270a202020202020202020202020202020202020202020414e4420712e6d6574686f646f6c6f67795f76657273696f6e5f7265636f72645f6964203d20732e66696e616e6369616c5f64645f76657273696f6e5f7265636f72645f6964290a202020202020202020202020202020202020290a20202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f434f5645524147455f53434f50455f494e56414c4944270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a2020202020202020202020204e45572e757064617465645f6174203a3d206e6f7728293b0a20202020202020202020202052455455524e204e45573b0a2020202020202020454e440a2020202020202020'),('public.idis_task6_erasure_requested(uuid,uuid)','0a20202020202020202020202053454c4543542045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d20657261737572655f726571756573747320650a20202020202020202020202020202020574845524520652e74656e616e745f6964203d20636865636b65645f74656e616e740a202020202020202020202020202020202020414e4420652e6465616c5f6964203d20636865636b65645f6465616c0a202020202020202020202020202020202020414e4420652e737461747573203d2027524551554553544544270a202020202020202020202020290a2020202020202020'),('public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)','0a20202020202020204445434c4152450a20202020202020202020202063757272656e745f74656e616e745f696420555549443b0a20202020202020202020202063757272656e745f6465616c5f696420555549443b0a20202020202020202020202063757272656e745f72756e5f696420555549443b0a20202020202020202020202063757272656e745f6f776e65725f696420555549443b0a20202020202020202020202063757272656e745f66656e63696e675f746f6b656e20424947494e543b0a2020202020202020424547494e0a202020202020202020202020424547494e0a2020202020202020202020202020202063757272656e745f74656e616e745f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f74656e616e745f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f6465616c5f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f6465616c5f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f72756e5f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f72756e5f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f6f776e65725f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f6f776e65725f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f66656e63696e675f746f6b656e203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f66656e63696e675f746f6b656e272c2074727565292c2027270a20202020202020202020202020202020293a3a626967696e743b0a202020202020202020202020455843455054494f4e205748454e204f5448455253205448454e0a2020202020202020202020202020202052455455524e2066616c73653b0a202020202020202020202020454e443b0a20202020202020202020202052455455524e2045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d2072756e7320720a20202020202020202020202020202020574845524520722e74656e616e745f6964203d20636865636b65645f74656e616e740a202020202020202020202020202020202020414e4420722e6465616c5f6964203d20636865636b65645f6465616c0a202020202020202020202020202020202020414e4420722e72756e5f6964203d20636865636b65645f72756e0a202020202020202020202020202020202020414e4420722e737461747573203d202752554e4e494e47270a202020202020202020202020202020202020414e4420722e74656e616e745f6964203d2063757272656e745f74656e616e745f69640a202020202020202020202020202020202020414e4420722e6465616c5f6964203d2063757272656e745f6465616c5f69640a202020202020202020202020202020202020414e4420722e72756e5f6964203d2063757272656e745f72756e5f69640a202020202020202020202020202020202020414e4420722e6c656173655f6f776e65725f6964203d2063757272656e745f6f776e65725f69640a202020202020202020202020202020202020414e4420722e6c656173655f66656e63696e675f746f6b656e203d2063757272656e745f66656e63696e675f746f6b656e0a202020202020202020202020202020202020414e4420722e6c656173655f657870697265735f6174203e2073746174656d656e745f74696d657374616d7028290a202020202020202020202020293b0a2020202020202020454e440a2020202020202020'),('public.idis_task6_immutable_guard()','0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d2027494e5345525427205448454e0a202020202020202020202020202020204946204e4f5420696469735f7461736b365f63757272656e745f6c656173655f69735f76616c6964280a20202020202020202020202020202020202020204e45572e74656e616e745f69642c204e45572e6465616c5f69642c204e45572e72756e5f69640a2020202020202020202020202020202029205448454e0a2020202020202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a20202020202020202020202020202020454e442049463b0a2020202020202020202020202020202052455455524e204e45573b0a202020202020202020202020454e442049463b0a20202020202020202020202049462054475f4f50203d202744454c455445270a202020202020202020202020202020414e4420696469735f7461736b365f657261737572655f726571756573746564284f4c442e74656e616e745f69642c204f4c442e6465616c5f696429205448454e0a2020202020202020202020202020202052455455524e204f4c443b0a202020202020202020202020454e442049463b0a202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f45585452414354494f4e5f5245434f52445f494d4d555441424c45270a202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a2020202020202020454e440a2020202020202020'),('public.idis_task6_attempt_guard()','0a20202020202020204445434c4152450a20202020202020202020202063757272656e745f74656e616e745f696420555549443b0a20202020202020202020202063757272656e745f6465616c5f696420555549443b0a20202020202020202020202063757272656e745f72756e5f696420555549443b0a20202020202020202020202063757272656e745f6f776e65725f696420555549443b0a20202020202020202020202063757272656e745f66656e63696e675f746f6b656e20424947494e543b0a2020202020202020424547494e0a20202020202020202020202049462054475f4f50203d202744454c45544527205448454e0a20202020202020202020202020202020494620696469735f7461736b365f657261737572655f726571756573746564284f4c442e74656e616e745f69642c204f4c442e6465616c5f696429205448454e0a202020202020202020202020202020202020202052455455524e204f4c443b0a20202020202020202020202020202020454e442049463b0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f45585452414354494f4e5f415454454d50545f494d4d555441424c45270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a202020202020202020202020424547494e0a2020202020202020202020202020202063757272656e745f74656e616e745f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f74656e616e745f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f6465616c5f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f6465616c5f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f72756e5f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f72756e5f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f6f776e65725f6964203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f6f776e65725f6964272c2074727565292c2027270a20202020202020202020202020202020293a3a757569643b0a2020202020202020202020202020202063757272656e745f66656e63696e675f746f6b656e203a3d204e554c4c4946280a202020202020202020202020202020202020202063757272656e745f73657474696e672827696469732e776f726b65725f6c656173655f66656e63696e675f746f6b656e272c2074727565292c2027270a20202020202020202020202020202020293a3a626967696e743b0a202020202020202020202020455843455054494f4e205748454e204f5448455253205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e443b0a2020202020202020202020204946204e4f542045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d2072756e7320720a20202020202020202020202020202020574845524520722e74656e616e745f6964203d204e45572e74656e616e745f69640a202020202020202020202020202020202020414e4420722e6465616c5f6964203d204e45572e6465616c5f69640a202020202020202020202020202020202020414e4420722e72756e5f6964203d204e45572e72756e5f69640a202020202020202020202020202020202020414e4420722e737461747573203d202752554e4e494e47270a202020202020202020202020202020202020414e4420722e74656e616e745f6964203d2063757272656e745f74656e616e745f69640a202020202020202020202020202020202020414e4420722e6465616c5f6964203d2063757272656e745f6465616c5f69640a202020202020202020202020202020202020414e4420722e72756e5f6964203d2063757272656e745f72756e5f69640a202020202020202020202020202020202020414e4420722e6c656173655f6f776e65725f6964203d2063757272656e745f6f776e65725f69640a202020202020202020202020202020202020414e4420722e6c656173655f66656e63696e675f746f6b656e203d2063757272656e745f66656e63696e675f746f6b656e0a202020202020202020202020202020202020414e4420722e6c656173655f657870697265735f6174203e2073746174656d656e745f74696d657374616d7028290a20202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204e4f542045584953545320280a2020202020202020202020202020202053454c45435420312046524f4d2072756e5f737465707320730a20202020202020202020202020202020574845524520732e74656e616e745f6964203d204e45572e74656e616e745f69640a202020202020202020202020202020202020414e4420732e72756e5f6964203d204e45572e72756e5f69640a202020202020202020202020202020202020414e4420732e737465705f6964203d204e45572e72756e5f737465705f69640a202020202020202020202020202020202020414e4420732e737465705f6e616d65203d20274d4554484f444f4c4f47595f45585452414354494f4e5f5441534b5f455845435554494f4e270a202020202020202020202020202020202020414e4420732e737465705f6f72646572203d20360a202020202020202020202020202020202020414e4420732e737461747573203d202752554e4e494e47270a20202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e442049463b0a20202020202020202020202049462054475f4f50203d2027494e5345525427205448454e0a202020202020202020202020202020204946204e45572e737461747573203c3e20275354415254454427205448454e0a2020202020202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f415454454d50545f4d5553545f5354415254270a2020202020202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a20202020202020202020202020202020454e442049463b0a202020202020202020202020202020204946204e45572e6c656173655f6f776e65725f6964203c3e2063757272656e745f6f776e65725f69640a202020202020202020202020202020202020204f52204e45572e6c656173655f66656e63696e675f746f6b656e203c3e2063757272656e745f66656e63696e675f746f6b656e205448454e0a2020202020202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a20202020202020202020202020202020454e442049463b0a2020202020202020202020202020202052455455524e204e45573b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204f4c442e737461747573203c3e202753544152544544270a2020202020202020202020202020204f52204e45572e737461747573204e4f5420494e202827535543434545444544272c20274641494c4544272c20274f5554434f4d455f554e4b4e4f574e27290a2020202020202020202020202020204f522028746f5f6a736f6e62284e455729202d2041525241595b0a202020202020202020202020202020202020202027737461747573272c20276f627365727665645f6d6f64656c272c202770726f76696465725f726571756573745f6964272c0a20202020202020202020202020202020202020202770726f76696465725f726573706f6e73655f736861323536272c2027646973706174636865645f63616c6c5f736861323536272c0a202020202020202020202020202020202020202027636f6d706c657465645f6174272c20276c6174656e63795f6d73272c0a202020202020202020202020202020202020202027696e7075745f746f6b656e73272c20276f75747075745f746f6b656e73272c202773746f705f726561736f6e272c0a2020202020202020202020202020202020202020276275646765745f7465726d696e616c5f7374617465272c2027726563656970745f736861323536272c2027736166655f6661696c7572655f636f6465270a2020202020202020202020202020202020205d3a3a746578745b5d290a20202020202020202020202020202020202049532044495354494e43542046524f4d0a20202020202020202020202020202020202028746f5f6a736f6e62284f4c4429202d2041525241595b0a202020202020202020202020202020202020202027737461747573272c20276f627365727665645f6d6f64656c272c202770726f76696465725f726571756573745f6964272c0a20202020202020202020202020202020202020202770726f76696465725f726573706f6e73655f736861323536272c2027646973706174636865645f63616c6c5f736861323536272c0a202020202020202020202020202020202020202027636f6d706c657465645f6174272c20276c6174656e63795f6d73272c0a202020202020202020202020202020202020202027696e7075745f746f6b656e73272c20276f75747075745f746f6b656e73272c202773746f705f726561736f6e272c0a2020202020202020202020202020202020202020276275646765745f7465726d696e616c5f7374617465272c2027726563656970745f736861323536272c2027736166655f6661696c7572655f636f6465270a2020202020202020202020202020202020205d3a3a746578745b5d29205448454e0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f45585452414354494f4e5f415454454d50545f494d4d555441424c45270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204e45572e73746174757320494e202827535543434545444544272c20274641494c454427290a202020202020202020202020202020414e4420280a202020202020202020202020202020202020202063757272656e745f6f776e65725f6964203c3e204f4c442e6c656173655f6f776e65725f69640a20202020202020202020202020202020202020204f522063757272656e745f66656e63696e675f746f6b656e203c3e204f4c442e6c656173655f66656e63696e675f746f6b656e0a20202020202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e442049463b0a2020202020202020202020204946204e45572e737461747573203d20274f5554434f4d455f554e4b4e4f574e270a202020202020202020202020202020414e44204e4f5420280a2020202020202020202020202020202020202020280a20202020202020202020202020202020202020202020202063757272656e745f6f776e65725f6964203d204f4c442e6c656173655f6f776e65725f69640a202020202020202020202020202020202020202020202020414e442063757272656e745f66656e63696e675f746f6b656e203d204f4c442e6c656173655f66656e63696e675f746f6b656e0a2020202020202020202020202020202020202020290a20202020202020202020202020202020202020204f522063757272656e745f66656e63696e675f746f6b656e203e204f4c442e6c656173655f66656e63696e675f746f6b656e0a20202020202020202020202020202029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e2027574f524b45525f4c454153455f46454e43454427205553494e4720455252434f4445203d20273430303031273b0a202020202020202020202020454e442049463b0a20202020202020202020202052455455524e204e45573b0a2020202020202020454e440a2020202020202020'),('public.idis_task6_validate_result_outputs()','0a20202020202020204445434c4152450a202020202020202020202020636865636b65645f726573756c7420555549443b0a202020202020202020202020636865636b65645f6f7574636f6d6520544558543b0a2020202020202020202020206f75747075745f636f756e7420424947494e543b0a2020202020202020424547494e0a202020202020202020202020636865636b65645f726573756c74203a3d20434f414c45534345284e45572e726573756c745f69642c204f4c442e726573756c745f6964293b0a20202020202020202020202053454c454354206f7574636f6d6520494e544f20636865636b65645f6f7574636f6d650a20202020202020202020202046524f4d206d6574686f646f6c6f67795f65787472616374696f6e5f726573756c74730a202020202020202020202020574845524520726573756c745f6964203d20636865636b65645f726573756c743b0a202020202020202020202020494620636865636b65645f6f7574636f6d65204953204e554c4c205448454e0a2020202020202020202020202020202052455455524e204e554c4c3b0a202020202020202020202020454e442049463b0a20202020202020202020202053454c45435420636f756e74282a2920494e544f206f75747075745f636f756e740a20202020202020202020202046524f4d206d6574686f646f6c6f67795f726573756c745f6661637475616c5f6f7574707574730a202020202020202020202020574845524520726573756c745f6964203d20636865636b65645f726573756c743b0a20202020202020202020202049462028636865636b65645f6f7574636f6d65203d202745564944454e43455f464f554e442720414e44206f75747075745f636f756e74203d2030290a2020202020202020202020202020204f522028636865636b65645f6f7574636f6d65203d202745564944454e43455f4e4f545f464f554e442720414e44206f75747075745f636f756e74203c3e203029205448454e0a20202020202020202020202020202020524149534520455843455054494f4e20274d4554484f444f4c4f47595f524553554c545f4641435455414c5f4f55545055545f4d49534d41544348270a20202020202020202020202020202020202020205553494e4720455252434f4445203d20273233353134273b0a202020202020202020202020454e442049463b0a20202020202020202020202052455455524e204e554c4c3b0a2020202020202020454e440a2020202020202020')) AS expected(function_signature,prosrc_utf8_hex) LEFT JOIN pg_catalog.pg_proc p ON p.oid=pg_catalog.to_regprocedure(expected.function_signature) WHERE p.oid IS NULL OR p.proowner<>(current_user)::pg_catalog.regrole OR p.prosrc<>pg_catalog.convert_from(pg_catalog.decode(expected.prosrc_utf8_hex,'hex'),'UTF8') OR p.prosecdef OR p.proconfig IS NOT NULL OR p.proacl IS NOT NULL) THEN RAISE EXCEPTION 'TASK7_RETAINED_0037_CATALOG_BASELINE_INVALID' USING ERRCODE='42501'; END IF; END$$",
)

_LEGACY_CONTRACT_HASH: Final = "6365adbfe07f1818fe9e8e634587feb59a6bda5238b625481940d9ea9efce723"
_LEGACY_SEMANTIC_VERSION: Final = "legacy-step-semantic-v1"
_LEGACY_SEMANTIC_HASH: Final = "4e743bd1264e407e40e48404a6ba081c111b1426bb798b09fb471085b98a18af"
_LEGACY_SNAPSHOT_STEPS: Final = (
    (0, "DATA_ROOM_INVENTORY_PACKAGE"),
    (1, "DATA_ROOM_INGESTION_HANDOFF"),
    (2, "INGEST_CHECK"),
    (3, "DOCUMENT_PREFLIGHT"),
    (4, "METHODOLOGY_COVERAGE_INIT"),
    (17, "EXTRACT"),
    (18, "GRADE"),
    (19, "CALC"),
)
_LEGACY_FULL_STEPS: Final = (
    (0, "DATA_ROOM_INVENTORY_PACKAGE"),
    (1, "DATA_ROOM_INGESTION_HANDOFF"),
    (2, "INGEST_CHECK"),
    (3, "DOCUMENT_PREFLIGHT"),
    (4, "METHODOLOGY_COVERAGE_INIT"),
    (5, "METHODOLOGY_EXTRACTION_TASK_PLANNING"),
    (6, "METHODOLOGY_EXTRACTION_TASK_EXECUTION"),
    (7, "METHODOLOGY_CLAIM_MATERIALIZATION"),
    (8, "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION"),
    (9, "METHODOLOGY_SANAD_CREATION_LINKING_GRADING"),
    (10, "METHODOLOGY_DETERMINISTIC_CALCULATION"),
    (11, "METHODOLOGY_TRUTH_DASHBOARD"),
    (12, "METHODOLOGY_EVIDENCE_TRUST_COURT"),
    (13, "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE"),
    (14, "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN"),
    (15, "METHODOLOGY_COMPANY_IDENTITY_PACKAGE"),
    (16, "METHODOLOGY_LAYER2_READINESS_PACKAGE"),
    (17, "EXTRACT"),
    (18, "GRADE"),
    (19, "CALC"),
    (20, "GRAPH_EVIDENCE"),
    (21, "RAG_EVIDENCE"),
    (22, "ENRICHMENT"),
    (23, "DEBATE"),
    (24, "LAYER2_IC_CHALLENGE"),
    (25, "ANALYSIS"),
    (26, "SCORING"),
    (27, "DELIVERABLES"),
)
_LEGACY_MODE_REGISTRY: Final = {
    "SNAPSHOT": ("snapshot-v1", _LEGACY_SNAPSHOT_STEPS),
    "FULL": ("full-v1", _LEGACY_FULL_STEPS),
}
BASELINE_PARENT_AUTHORITIES_V1: Final = (
    (
        0,
        "claims",
        "ux_claims_task6_scope",
        "UNIQUE_INDEX",
        ("tenant_id", "deal_id", "claim_id"),
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_claims_task6_scope ON claims (tenant_id, deal_id, claim_id)",
        "PRESERVE_PRE_0038",
    ),
    (
        1,
        "deals",
        "uq_task7_deals_tenant_deal",
        "UNIQUE_CONSTRAINT",
        ("tenant_id", "deal_id"),
        None,
        "0038",
        "ALTER TABLE deals ADD CONSTRAINT uq_task7_deals_tenant_deal UNIQUE (tenant_id,deal_id)",
        "DROP_WITH_0038",
    ),
    (
        2,
        "evidence_items",
        "uq_v2_evidence_item_generic_parent",
        "UNIQUE_CONSTRAINT",
        ("tenant_id", "deal_id", "evidence_id", "claim_id", "source_span_id"),
        None,
        "0038",
        "ALTER TABLE evidence_items ADD CONSTRAINT "
        "uq_v2_evidence_item_generic_parent "
        "UNIQUE (tenant_id,deal_id,evidence_id,claim_id,source_span_id)",
        "DROP_WITH_0038",
    ),
    (
        3,
        "sanads",
        "ux_sanads_task6_scope",
        "UNIQUE_INDEX",
        ("tenant_id", "deal_id", "sanad_id"),
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_sanads_task6_scope ON sanads (tenant_id, deal_id, sanad_id)",
        "PRESERVE_PRE_0038",
    ),
)
BASELINE_PARENT_AUTHORITIES_0038_V1: Final = (
    BASELINE_PARENT_AUTHORITIES_V1[1],
    BASELINE_PARENT_AUTHORITIES_V1[2],
)
_DDL_ROWS: Final[dict[str, dict[str, Any]]] = {
    "deliverable_manifest_references": {
        "column_tuple": [
            "manifest_reference_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "manifest_deliverable_id UUID NOT NULL",
            "manifest_output_object_id UUID NOT NULL",
            "manifest_catalog_ordinal INTEGER GENERATED ALWAYS AS (14) STORED",
            "run_output_object_id UUID NOT NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED "
            "ALWAYS AS "
            "('DELIVERABLE_MANIFEST_REFERENCE') STORED",
            "member_kind TEXT NOT NULL",
            "catalog_ordinal INTEGER NULL",
            "member_step_output_id UUID NOT NULL",
            "member_output_object_id UUID NOT NULL",
            "member_identity_sha256 TEXT NOT NULL",
            "member_value_sha256 TEXT NOT NULL",
            "member_content_sha256 TEXT NULL",
            "reference_sha256 TEXT NOT NULL",
            "lease_owner_id UUID NOT NULL",
            "lease_fencing_token BIGINT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ],
        "check_tuple": [
            "ck_task7_deliverable_manifest_references_member_kind(member_kind "
            "IN ('ARTIFACT','RECONCILIATION'))",
            "ck_task7_deliverable_manifest_references_coordinate((member_kind='ARTIFACT' "
            "AND catalog_ordinal BETWEEN 0 AND 13 AND "
            "member_content_sha256 IS NOT NULL) OR "
            "(member_kind='RECONCILIATION' AND "
            "catalog_ordinal IS NULL AND "
            "member_content_sha256 IS NULL))",
            "ck_task7_deliverable_manifest_references_hashes(member_identity_sha256~'^[0-9a-f]{64}$' "
            "AND member_value_sha256~'^[0-9a-f]{64}$' AND "
            "reference_sha256~'^[0-9a-f]{64}$' AND "
            "(member_content_sha256 IS NULL OR "
            "member_content_sha256~'^[0-9a-f]{64}$'))",
            "ck_task7_deliverable_manifest_references_distinct_roots(member_output_object_id<>manifest_output_object_id "
            "AND "
            "run_output_object_id<>manifest_output_object_id "
            "AND "
            "run_output_object_id<>member_output_object_id)",
            "ck_task7_deliverable_manifest_references_lease(lease_owner_id "
            "IS NOT NULL AND lease_fencing_token>0)",
        ],
        "foreign_key_tuple": [
            "fk_task7_deliverable_manifest_references_run(tenant_id,deal_id,run_id) "
            "-> "
            "runs.uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id) "
            "ON UPDATE NO ACTION ON DELETE NO "
            "ACTION USING "
            "ix_task7_deliverable_manifest_references_run_fk",
            "fk_task7_deliverable_manifest_references_manifest(tenant_id,deal_id,run_id,manifest_deliverable_id,manifest_output_object_id,manifest_catalog_ordinal) "
            "-> "
            "deliverables.uq_task7_deliverables_manifest_parent(tenant_id,deal_id,run_id,deliverable_id,run_output_object_id,catalog_ordinal) "
            "ON UPDATE NO ACTION ON DELETE NO "
            "ACTION USING "
            "ix_task7_deliverable_manifest_references_manifest_fk",
            "fk_task7_deliverable_manifest_references_manifest_root(tenant_id,deal_id,run_id,manifest_output_object_id) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_scope(tenant_id,deal_id,run_id,output_object_id) "
            "ON UPDATE NO ACTION ON DELETE NO "
            "ACTION USING "
            "ix_task7_deliverable_manifest_references_manifest_fk",
            "fk_task7_deliverable_manifest_references_native_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO "
            "ACTION USING "
            "ix_task7_deliverable_manifest_references_native_root_fk",
            "fk_task7_deliverable_manifest_references_member_step(tenant_id,deal_id,run_id,member_step_output_id,member_output_object_id) "
            "-> "
            "run_step_outputs.uq_task7_run_step_outputs_scoped_output(tenant_id,deal_id,run_id,step_output_id,output_object_id) "
            "ON UPDATE NO ACTION ON DELETE NO "
            "ACTION USING "
            "ix_task7_deliverable_manifest_references_member_step_fk",
            "fk_task7_deliverable_manifest_references_member_root(tenant_id,deal_id,run_id,member_output_object_id) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_scope(tenant_id,deal_id,run_id,output_object_id) "
            "ON UPDATE NO ACTION ON DELETE NO "
            "ACTION USING "
            "ix_task7_deliverable_manifest_references_member_root_fk",
        ],
        "unique_tuple": [
            "pk_task7_deliverable_manifest_references(manifest_reference_id)",
            "uq_task7_deliverable_manifest_references_scoped_id(tenant_id,deal_id,run_id,manifest_reference_id)",
            "uq_task7_deliverable_manifest_references_native_root(run_output_object_id)",
            "uq_task7_deliverable_manifest_references_coordinate(tenant_id,run_id,manifest_deliverable_id,member_kind,catalog_ordinal) "
            "NULLS NOT DISTINCT",
            "uq_task7_deliverable_manifest_references_member(tenant_id,run_id,manifest_deliverable_id,member_output_object_id)",
            "uq_task7_deliverable_manifest_references_reference_sha(tenant_id,run_id,manifest_deliverable_id,reference_sha256)",
        ],
        "index_tuple": [
            "ix_task7_deliverable_manifest_references_run_fk(tenant_id,deal_id,run_id)",
            "ix_task7_deliverable_manifest_references_manifest_fk(tenant_id,deal_id,run_id,manifest_deliverable_id,manifest_output_object_id,manifest_catalog_ordinal)",
            "ix_task7_deliverable_manifest_references_native_root_fk(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
            "ix_task7_deliverable_manifest_references_member_step_fk(tenant_id,deal_id,run_id,member_step_output_id,member_output_object_id)",
            "ix_task7_deliverable_manifest_references_member_root_fk(tenant_id,deal_id,run_id,member_output_object_id)",
        ],
        "rls_tuple": [
            "ALTER TABLE deliverable_manifest_references ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE deliverable_manifest_references FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "deliverable_manifest_references_tenant_isolation "
            "ON deliverable_manifest_references AS "
            "PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
            "REVOKE ALL PRIVILEGES ON TABLE deliverable_manifest_references FROM PUBLIC",
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "
            "deliverable_manifest_references TO "
            "<configured_app_role>",
        ],
        "erasure_ordinal": 0,
    },
    "run_consumer_receipts": {
        "column_tuple": [
            "receipt_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "expected_edge_id UUID NOT NULL",
            "consumer_step_output_id UUID NOT NULL",
            "consumer_output_object_id UUID NOT NULL",
            "consumer_step_name VARCHAR(100) NOT NULL",
            "consumer_subrole TEXT NOT NULL",
            "usage_evidence_sha256 TEXT NOT NULL",
            "consumed_at TIMESTAMPTZ NOT NULL",
            "lease_owner_id UUID NOT NULL",
            "lease_fencing_token BIGINT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ],
        "check_tuple": [
            "ck_task7_run_consumer_receipts_usage_sha(usage_evidence_sha256~'^[0-9a-f]{64}$')",
            "ck_task7_run_consumer_receipts_nonblank(btrim(consumer_step_name)<>'' "
            "AND btrim(consumer_subrole)<>'')",
            "ck_task7_run_consumer_receipts_lease(lease_owner_id IS "
            "NOT NULL AND lease_fencing_token>0)",
        ],
        "foreign_key_tuple": [
            "fk_task7_run_consumer_receipts_run(tenant_id,deal_id,run_id) "
            "-> "
            "runs.uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_run_consumer_receipts_consumer",
            "fk_task7_run_consumer_receipts_edge(tenant_id,deal_id,run_id,expected_edge_id,consumer_step_name,consumer_subrole) "
            "-> "
            "run_expected_consumer_edges.uq_task7_run_expected_consumer_edges_consumer_tuple(tenant_id,deal_id,run_id,expected_edge_id,consumer_step_name,consumer_subrole) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_run_consumer_receipts_edge_fk",
            "fk_task7_run_consumer_receipts_consumer_output(tenant_id,deal_id,run_id,consumer_step_output_id,consumer_output_object_id,consumer_step_name,consumer_subrole) "
            "-> "
            "run_step_outputs.uq_task7_run_step_outputs_receipt_tuple(tenant_id,deal_id,run_id,step_output_id,output_object_id,step_name,subrole) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_run_consumer_receipts_consumer_output",
        ],
        "unique_tuple": [
            "pk_task7_run_consumer_receipts(receipt_id)",
            "uq_task7_run_consumer_receipts_scoped_id(tenant_id,deal_id,run_id,receipt_id)",
            "uq_task7_run_consumer_receipts_edge_output(tenant_id,run_id,expected_edge_id,consumer_output_object_id)",
            "uq_task7_run_consumer_receipts_one_per_edge(tenant_id,run_id,expected_edge_id)",
        ],
        "index_tuple": [
            "ix_task7_run_consumer_receipts_edge_fk(tenant_id,deal_id,run_id,expected_edge_id,consumer_step_name,consumer_subrole)",
            "ix_task7_run_consumer_receipts_consumer_output(tenant_id,deal_id,run_id,consumer_step_output_id,consumer_output_object_id,consumer_step_name,consumer_subrole)",
            "ix_task7_run_consumer_receipts_consumer(tenant_id,run_id,consumer_step_name,consumer_subrole,receipt_id)",
        ],
        "rls_tuple": [
            "ALTER TABLE run_consumer_receipts ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE run_consumer_receipts FORCE ROW LEVEL SECURITY",
            "CREATE POLICY run_consumer_receipts_tenant_isolation ON "
            "run_consumer_receipts AS PERMISSIVE FOR ALL TO PUBLIC "
            "USING (NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
            "REVOKE ALL PRIVILEGES ON TABLE run_consumer_receipts FROM PUBLIC",
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "
            "run_consumer_receipts TO <configured_app_role>",
        ],
        "erasure_ordinal": 0,
    },
    "run_expected_consumer_edges": {
        "column_tuple": [
            "expected_edge_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "producer_input_binding_id UUID NULL",
            "producer_output_object_id UUID NULL",
            "consumer_step_name VARCHAR(100) NOT NULL",
            "consumer_subrole TEXT NOT NULL",
            "usage_kind TEXT NOT NULL",
            "applicability_decision_id UUID NULL",
            "contract_rule_id TEXT NOT NULL",
            "contract_rule_version TEXT NOT NULL",
            "contract_rule_hash TEXT NOT NULL",
            "edge_tuple_sha256 TEXT NOT NULL",
            "materialization_origin TEXT NOT NULL",
            "lease_owner_id UUID NULL",
            "lease_fencing_token BIGINT NOT NULL DEFAULT 0",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ],
        "check_tuple": [
            "ck_task7_run_expected_consumer_edges_producer_xor((producer_input_binding_id "
            "IS NULL)<>(producer_output_object_id IS NULL))",
            "ck_task7_run_expected_consumer_edges_origin(materialization_origin "
            "IN ('ADMISSION','WORKER'))",
            "ck_task7_run_expected_consumer_edges_hashes(contract_rule_hash~'^[0-9a-f]{64}$' "
            "AND edge_tuple_sha256~'^[0-9a-f]{64}$')",
            "ck_task7_run_expected_consumer_edges_nonblank(btrim(consumer_step_name)<>'' "
            "AND btrim(consumer_subrole)<>'' AND "
            "btrim(usage_kind)<>'' AND "
            "btrim(contract_rule_id)<>'' AND "
            "btrim(contract_rule_version)<>'')",
            "ck_task7_run_expected_consumer_edges_lease((materialization_origin='ADMISSION' "
            "AND lease_owner_id IS NULL AND "
            "lease_fencing_token=0) OR "
            "(materialization_origin='WORKER' AND "
            "lease_owner_id IS NOT NULL AND "
            "lease_fencing_token>0))",
        ],
        "foreign_key_tuple": [
            "fk_task7_run_expected_consumer_edges_run(tenant_id,deal_id,run_id) "
            "-> "
            "runs.uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_run_expected_consumer_edges_run_fk",
            "fk_task7_run_expected_consumer_edges_input(tenant_id,deal_id,run_id,producer_input_binding_id) "
            "-> "
            "run_input_bindings.uq_task7_run_input_bindings_scoped_id(tenant_id,deal_id,run_id,binding_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_run_expected_consumer_edges_input",
            "fk_task7_run_expected_consumer_edges_output(tenant_id,deal_id,run_id,producer_output_object_id) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_scope(tenant_id,deal_id,run_id,output_object_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_run_expected_consumer_edges_output",
            "fk_task7_run_expected_consumer_edges_decision(tenant_id,deal_id,run_id,applicability_decision_id) "
            "-> "
            "run_applicability_decisions.uq_task7_run_applicability_decisions_scoped_id(tenant_id,deal_id,run_id,applicability_decision_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_run_expected_consumer_edges_decision",
            "fk_task7_run_expected_consumer_edges_consumer_step(tenant_id,run_id,consumer_step_name) "
            "-> "
            "run_steps.uq_task7_run_steps_tenant_run_name(tenant_id,run_id,step_name) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_run_expected_consumer_edges_consumer",
        ],
        "unique_tuple": [
            "pk_task7_run_expected_consumer_edges(expected_edge_id)",
            "uq_task7_run_expected_consumer_edges_scoped_id(tenant_id,deal_id,run_id,expected_edge_id)",
            "uq_task7_run_expected_consumer_edges_consumer_tuple(tenant_id,deal_id,run_id,expected_edge_id,consumer_step_name,consumer_subrole)",
            "uq_task7_run_expected_consumer_edges_tuple_sha(tenant_id,run_id,edge_tuple_sha256)",
            "uq_task7_run_expected_consumer_edges_natural(tenant_id,run_id,producer_input_binding_id,producer_output_object_id,consumer_step_name,consumer_subrole,usage_kind,applicability_decision_id,contract_rule_id,contract_rule_version) "
            "NULLS NOT DISTINCT",
        ],
        "index_tuple": [
            "ix_task7_run_expected_consumer_edges_run_fk(tenant_id,deal_id,run_id)",
            "ix_task7_run_expected_consumer_edges_input(tenant_id,deal_id,run_id,producer_input_binding_id) "
            "WHERE producer_input_binding_id IS NOT NULL",
            "ix_task7_run_expected_consumer_edges_output(tenant_id,deal_id,run_id,producer_output_object_id) "
            "WHERE producer_output_object_id IS NOT NULL",
            "ix_task7_run_expected_consumer_edges_consumer(tenant_id,run_id,consumer_step_name,consumer_subrole,expected_edge_id)",
            "ix_task7_run_expected_consumer_edges_decision(tenant_id,deal_id,run_id,applicability_decision_id) "
            "WHERE applicability_decision_id IS NOT NULL",
        ],
        "rls_tuple": [
            "ALTER TABLE run_expected_consumer_edges ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE run_expected_consumer_edges FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "run_expected_consumer_edges_tenant_isolation ON "
            "run_expected_consumer_edges AS PERMISSIVE FOR ALL "
            "TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
            "REVOKE ALL PRIVILEGES ON TABLE run_expected_consumer_edges FROM PUBLIC",
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "
            "run_expected_consumer_edges TO "
            "<configured_app_role>",
        ],
        "erasure_ordinal": 1,
    },
    "run_applicability_decisions": {
        "column_tuple": [
            "applicability_decision_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "decision_phase TEXT NOT NULL",
            "decision_kind TEXT NOT NULL",
            "subject_kind TEXT NOT NULL",
            "subject_key TEXT NOT NULL",
            "outcome TEXT NOT NULL",
            "upstream_input_binding_id UUID NULL",
            "upstream_output_object_id UUID NULL",
            "deciding_step_output_id UUID NOT NULL",
            "contract_rule_id TEXT NOT NULL",
            "contract_rule_version TEXT NOT NULL",
            "contract_rule_hash TEXT NOT NULL",
            "evidence_sha256 TEXT NOT NULL",
            "decision_sha256 TEXT NOT NULL",
            "lease_owner_id UUID NOT NULL",
            "lease_fencing_token BIGINT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ],
        "check_tuple": [
            "ck_task7_run_applicability_decisions_phase(decision_phase IN ('STEP_5','STEP_16'))",
            "ck_task7_run_applicability_decisions_outcome(outcome "
            "IN ('APPLICABLE','NOT_APPLICABLE'))",
            "ck_task7_run_applicability_decisions_upstream_xor((upstream_input_binding_id "
            "IS NULL)<>(upstream_output_object_id IS NULL))",
            "ck_task7_run_applicability_decisions_hashes(contract_rule_hash~'^[0-9a-f]{64}$' "
            "AND evidence_sha256~'^[0-9a-f]{64}$' AND "
            "decision_sha256~'^[0-9a-f]{64}$')",
            "ck_task7_run_applicability_decisions_nonblank(btrim(decision_kind)<>'' "
            "AND btrim(subject_kind)<>'' AND "
            "btrim(subject_key)<>'' AND "
            "btrim(contract_rule_id)<>'' AND "
            "btrim(contract_rule_version)<>'')",
            "ck_task7_run_applicability_decisions_lease(lease_owner_id "
            "IS NOT NULL AND lease_fencing_token>0)",
        ],
        "foreign_key_tuple": [
            "fk_task7_run_applicability_decisions_run(tenant_id,deal_id,run_id) "
            "-> "
            "runs.uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_run_applicability_decisions_subject",
            "fk_task7_run_applicability_decisions_input(tenant_id,deal_id,run_id,upstream_input_binding_id) "
            "-> "
            "run_input_bindings.uq_task7_run_input_bindings_scoped_id(tenant_id,deal_id,run_id,binding_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_run_applicability_decisions_input",
            "fk_task7_run_applicability_decisions_output(tenant_id,deal_id,run_id,upstream_output_object_id) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_scope(tenant_id,deal_id,run_id,output_object_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_run_applicability_decisions_output",
            "fk_task7_run_applicability_decisions_deciding_output(tenant_id,deal_id,run_id,deciding_step_output_id) "
            "-> "
            "run_step_outputs.uq_task7_run_step_outputs_scoped_id(tenant_id,deal_id,run_id,step_output_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_run_applicability_decisions_deciding",
        ],
        "unique_tuple": [
            "pk_task7_run_applicability_decisions(applicability_decision_id)",
            "uq_task7_run_applicability_decisions_scoped_id(tenant_id,deal_id,run_id,applicability_decision_id)",
            "uq_task7_run_applicability_decisions_subject(tenant_id,run_id,decision_phase,decision_kind,subject_kind,subject_key,contract_rule_id,contract_rule_version)",
            "uq_task7_run_applicability_decisions_decision_sha(tenant_id,run_id,decision_sha256)",
        ],
        "index_tuple": [
            "ix_task7_run_applicability_decisions_subject(tenant_id,run_id,decision_phase,subject_kind,subject_key)",
            "ix_task7_run_applicability_decisions_input(tenant_id,deal_id,run_id,upstream_input_binding_id) "
            "WHERE upstream_input_binding_id IS NOT NULL",
            "ix_task7_run_applicability_decisions_output(tenant_id,deal_id,run_id,upstream_output_object_id) "
            "WHERE upstream_output_object_id IS NOT NULL",
            "ix_task7_run_applicability_decisions_deciding(tenant_id,deal_id,run_id,deciding_step_output_id)",
        ],
        "rls_tuple": [
            "ALTER TABLE run_applicability_decisions ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE run_applicability_decisions FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "run_applicability_decisions_tenant_isolation ON "
            "run_applicability_decisions AS PERMISSIVE FOR ALL "
            "TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
            "REVOKE ALL PRIVILEGES ON TABLE run_applicability_decisions FROM PUBLIC",
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "
            "run_applicability_decisions TO "
            "<configured_app_role>",
        ],
        "erasure_ordinal": 2,
    },
    "run_step_outputs": {
        "column_tuple": [
            "step_output_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "step_id UUID NOT NULL",
            "output_object_id UUID NOT NULL",
            "step_name VARCHAR(100) NOT NULL",
            "subrole TEXT NOT NULL DEFAULT '__step__'",
            "output_kind TEXT NOT NULL",
            "output_ordinal INTEGER NOT NULL",
            "semantic_rule_version TEXT NOT NULL",
            "semantic_rule_hash TEXT NOT NULL",
            "summary_sha256 TEXT NOT NULL",
            "lease_owner_id UUID NOT NULL",
            "lease_fencing_token BIGINT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ],
        "check_tuple": [
            "ck_task7_run_step_outputs_ordinal(output_ordinal>=0)",
            "ck_task7_run_step_outputs_hashes(semantic_rule_hash~'^[0-9a-f]{64}$' "
            "AND summary_sha256~'^[0-9a-f]{64}$')",
            "ck_task7_run_step_outputs_nonblank(btrim(step_name)<>'' AND "
            "btrim(subrole)<>'' AND btrim(output_kind)<>'' AND "
            "btrim(semantic_rule_version)<>'')",
            "ck_task7_run_step_outputs_lease(lease_owner_id IS NOT NULL AND lease_fencing_token>0)",
        ],
        "foreign_key_tuple": [
            "fk_task7_run_step_outputs_step(tenant_id,run_id,step_id,step_name) "
            "-> "
            "run_steps.uq_task7_run_steps_scoped_id_name(tenant_id,run_id,step_id,step_name) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_run_step_outputs_step_fk",
            "fk_task7_run_step_outputs_output(tenant_id,deal_id,run_id,output_object_id,output_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_step_ref(tenant_id,deal_id,run_id,output_object_id,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_run_step_outputs_output_fk",
        ],
        "unique_tuple": [
            "pk_task7_run_step_outputs(step_output_id)",
            "uq_task7_run_step_outputs_output_object(output_object_id)",
            "uq_task7_run_step_outputs_scoped_id(tenant_id,deal_id,run_id,step_output_id)",
            "uq_task7_run_step_outputs_scoped_output(tenant_id,deal_id,run_id,step_output_id,output_object_id)",
            "uq_task7_run_step_outputs_coordinate(tenant_id,run_id,step_id,subrole,output_kind,output_ordinal)",
            "uq_task7_run_step_outputs_receipt_tuple(tenant_id,deal_id,run_id,step_output_id,output_object_id,step_name,subrole)",
        ],
        "index_tuple": [
            "ix_task7_run_step_outputs_step_fk(tenant_id,run_id,step_id,step_name)",
            "ix_task7_run_step_outputs_output_fk(tenant_id,deal_id,run_id,output_object_id,output_kind)",
            "ix_task7_run_step_outputs_consumer_lookup(tenant_id,run_id,step_name,subrole,output_kind,output_ordinal)",
        ],
        "rls_tuple": [
            "ALTER TABLE run_step_outputs ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE run_step_outputs FORCE ROW LEVEL SECURITY",
            "CREATE POLICY run_step_outputs_tenant_isolation ON "
            "run_step_outputs AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK (NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
            "REVOKE ALL PRIVILEGES ON TABLE run_step_outputs FROM PUBLIC",
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE run_step_outputs TO <configured_app_role>",
        ],
        "erasure_ordinal": 3,
    },
    "methodology_result_claim_links": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('METHODOLOGY_CLAIM_LINK') STORED",
        ],
        "check_tuple": [
            "ck_task7_methodology_result_claim_links_output_shape(run_output_object_id "
            "IS NULL OR "
            "(run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='METHODOLOGY_CLAIM_LINK'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_methodology_result_claim_links_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_methodology_result_claim_links_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_methodology_result_claim_links_output_root(run_output_object_id)"
        ],
        "index_tuple": [
            "ix_task7_methodology_result_claim_links_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE methodology_result_claim_links ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE methodology_result_claim_links FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "methodology_result_claim_links_tenant_isolation "
            "ON methodology_result_claim_links AS PERMISSIVE "
            "FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 4,
    },
    "methodology_result_factual_outputs": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('METHODOLOGY_FACTUAL_OUTPUT') STORED",
        ],
        "check_tuple": [
            "ck_task7_methodology_result_factual_outputs_output_shape(run_output_object_id "
            "IS NULL OR "
            "(run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='METHODOLOGY_FACTUAL_OUTPUT'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_methodology_result_factual_outputs_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO "
            "ACTION USING "
            "ix_task7_methodology_result_factual_outputs_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_methodology_result_factual_outputs_output_root(run_output_object_id)"
        ],
        "index_tuple": [
            "ix_task7_methodology_result_factual_outputs_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE methodology_result_factual_outputs ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE methodology_result_factual_outputs FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "methodology_result_factual_outputs_tenant_isolation "
            "ON methodology_result_factual_outputs AS "
            "PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 5,
    },
    "methodology_extraction_results": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS "
            "AS ('METHODOLOGY_EXTRACTION_RESULT') STORED",
        ],
        "check_tuple": [
            "ck_task7_methodology_extraction_results_output_shape(run_output_object_id "
            "IS NULL OR "
            "(run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='METHODOLOGY_EXTRACTION_RESULT'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_methodology_extraction_results_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_methodology_extraction_results_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_methodology_extraction_results_output_root(run_output_object_id)"
        ],
        "index_tuple": [
            "ix_task7_methodology_extraction_results_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE methodology_extraction_results ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE methodology_extraction_results FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "methodology_extraction_results_tenant_isolation "
            "ON methodology_extraction_results AS PERMISSIVE "
            "FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 6,
    },
    "methodology_extraction_tasks": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS "
            "AS ('METHODOLOGY_EXTRACTION_TASK') STORED",
        ],
        "check_tuple": [
            "ck_task7_methodology_extraction_tasks_output_shape(run_output_object_id "
            "IS NULL OR "
            "(run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='METHODOLOGY_EXTRACTION_TASK'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_methodology_extraction_tasks_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_methodology_extraction_tasks_output_root_scope"
        ],
        "unique_tuple": ["uq_task7_methodology_extraction_tasks_output_root(run_output_object_id)"],
        "index_tuple": [
            "ix_task7_methodology_extraction_tasks_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE methodology_extraction_tasks ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE methodology_extraction_tasks FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "methodology_extraction_tasks_tenant_isolation ON "
            "methodology_extraction_tasks AS PERMISSIVE FOR "
            "ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 7,
    },
    "methodology_coverage_records": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS "
            "AS ('METHODOLOGY_COVERAGE_IDENTITY') STORED",
        ],
        "check_tuple": [
            "ck_task7_methodology_coverage_records_output_shape(run_output_object_id "
            "IS NULL OR "
            "(run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='METHODOLOGY_COVERAGE_IDENTITY'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_methodology_coverage_records_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_methodology_coverage_records_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_methodology_coverage_records_output_root(run_output_object_id)",
            "uq_task7_methodology_coverage_calculation_parent(tenant_id,deal_id,run_id,coverage_record_id,methodology_question_record_id)",
            "uq_v2_coverage_dashboard_parent(tenant_id,deal_id,run_id,coverage_record_id)",
        ],
        "index_tuple": [
            "ix_task7_methodology_coverage_records_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE methodology_coverage_records ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE methodology_coverage_records FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "methodology_coverage_records_tenant_isolation ON "
            "methodology_coverage_records AS PERMISSIVE FOR "
            "ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 8,
    },
    "calc_sanads": {
        "column_tuple": ["deal_id UUID NULL", "run_id UUID NULL"],
        "check_tuple": [
            "ck_task7_calc_sanads_scope((deal_id IS NULL AND run_id IS NULL) "
            "OR (deal_id IS NOT NULL AND run_id IS NOT NULL))"
        ],
        "foreign_key_tuple": [
            "fk_task7_calc_sanads_calculation(tenant_id,deal_id,run_id,calc_id) "
            "-> "
            "deterministic_calculations.uq_task7_deterministic_calculations_scope(tenant_id,deal_id,run_id,calc_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_calc_sanads_calculation_fk"
        ],
        "unique_tuple": ["uq_task7_calc_sanads_scope(tenant_id,deal_id,run_id,calc_sanad_id)"],
        "index_tuple": ["ix_task7_calc_sanads_calculation_fk(tenant_id,deal_id,run_id,calc_id)"],
        "rls_tuple": [
            "ALTER TABLE calc_sanads ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE calc_sanads FORCE ROW LEVEL SECURITY",
            "CREATE POLICY calc_sanads_tenant_isolation ON calc_sanads AS "
            "PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK (NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 9,
    },
    "deterministic_calculations": {
        "column_tuple": [
            "run_id UUID NULL",
            "producer_step_name VARCHAR(100) NULL",
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT NULL",
            "coverage_record_id TEXT NULL",
            "methodology_question_record_id UUID NULL",
        ],
        "check_tuple": [
            "ck_task7_deterministic_calculations_output_shape((producer_step_name "
            "IS NULL AND run_output_object_id IS NULL AND "
            "run_output_object_kind IS NULL AND "
            "coverage_record_id IS NULL AND "
            "methodology_question_record_id IS NULL) OR "
            "(run_id IS NOT NULL AND run_output_object_id IS "
            "NOT NULL AND run_output_object_class='NATIVE_ROW' "
            "AND "
            "((producer_step_name='METHODOLOGY_DETERMINISTIC_CALCULATION' "
            "AND "
            "run_output_object_kind='METHODOLOGY_CALCULATION' "
            "AND coverage_record_id IS NOT NULL AND "
            "methodology_question_record_id IS NOT NULL) OR "
            "(producer_step_name='CALC' AND "
            "run_output_object_kind='CALCULATION' AND "
            "coverage_record_id IS NULL AND "
            "methodology_question_record_id IS NULL))))"
        ],
        "foreign_key_tuple": [
            "fk_task7_deterministic_calculations_run(tenant_id,deal_id,run_id) "
            "-> "
            "runs.uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_deterministic_calculations_run_fk",
            "fk_task7_deterministic_calculations_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_deterministic_calculations_output_root_scope",
            "fk_task7_deterministic_calculations_coverage_question(tenant_id,deal_id,run_id,coverage_record_id,methodology_question_record_id) "
            "-> "
            "methodology_coverage_records.uq_task7_methodology_coverage_calculation_parent(tenant_id,deal_id,run_id,coverage_record_id,methodology_question_record_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_deterministic_calculations_coverage_question",
        ],
        "unique_tuple": [
            "uq_task7_deterministic_calculations_scope(tenant_id,deal_id,run_id,calc_id)",
            "uq_task7_deterministic_calculations_output_root(run_output_object_id)",
        ],
        "index_tuple": [
            "ix_task7_deterministic_calculations_run_fk(tenant_id,deal_id,run_id)",
            "ix_task7_deterministic_calculations_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
            "ix_task7_deterministic_calculations_coverage_question(tenant_id,deal_id,run_id,coverage_record_id,methodology_question_record_id)",
        ],
        "rls_tuple": [
            "ALTER TABLE deterministic_calculations ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE deterministic_calculations FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "deterministic_calculations_tenant_isolation ON "
            "deterministic_calculations AS PERMISSIVE FOR ALL TO "
            "PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 10,
    },
    "layer2_ic_findings": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('LAYER2_FINDING') STORED",
        ],
        "check_tuple": [
            "ck_task7_layer2_ic_findings_output_shape(run_output_object_id "
            "IS NULL OR (run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='LAYER2_FINDING'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_layer2_ic_findings_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_layer2_ic_findings_output_root_scope"
        ],
        "unique_tuple": ["uq_task7_layer2_ic_findings_output_root(run_output_object_id)"],
        "index_tuple": [
            "ix_task7_layer2_ic_findings_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE layer2_ic_findings ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE layer2_ic_findings FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_layer2_ic_findings ON "
            "layer2_ic_findings AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 11,
    },
    "layer2_ic_challenges": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('LAYER2_CHALLENGE') STORED",
        ],
        "check_tuple": [
            "ck_task7_layer2_ic_challenges_output_shape(run_output_object_id "
            "IS NULL OR (run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='LAYER2_CHALLENGE'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_layer2_ic_challenges_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_layer2_ic_challenges_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_layer2_ic_challenges_output_root(run_output_object_id)",
            "uq_v2_layer2_challenge_scope(tenant_id,deal_id,run_id,challenge_id)",
        ],
        "index_tuple": [
            "ix_task7_layer2_ic_challenges_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE layer2_ic_challenges ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE layer2_ic_challenges FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_layer2_ic_challenges ON "
            "layer2_ic_challenges AS PERMISSIVE FOR ALL TO PUBLIC "
            "USING (NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 12,
    },
    "debate_sessions": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('DEBATE_SESSION') STORED",
        ],
        "check_tuple": [
            "ck_task7_debate_sessions_output_shape(run_output_object_id "
            "IS NULL OR (run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='DEBATE_SESSION'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_debate_sessions_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_debate_sessions_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_debate_sessions_output_root(run_output_object_id)",
            "uq_v2_debate_session_scope(tenant_id,deal_id,run_id,debate_id)",
        ],
        "index_tuple": [
            "ix_task7_debate_sessions_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE debate_sessions ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE debate_sessions FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_debate_sessions ON "
            "debate_sessions AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL "
            "AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK (NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 13,
    },
    "evidence_trust_findings": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('EVIDENCE_TRUST_FINDING') STORED",
        ],
        "check_tuple": [
            "ck_task7_evidence_trust_findings_output_shape(run_output_object_id "
            "IS NULL OR (run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='EVIDENCE_TRUST_FINDING'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_evidence_trust_findings_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_evidence_trust_findings_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_evidence_trust_findings_output_root(run_output_object_id)",
            "uq_v2_evidence_trust_findings_reference_parent(tenant_id,deal_id,run_id,finding_id,run_output_object_id)",
        ],
        "index_tuple": [
            "ix_task7_evidence_trust_findings_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE evidence_trust_findings ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE evidence_trust_findings FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_evidence_trust_findings "
            "ON evidence_trust_findings AS PERMISSIVE FOR ALL TO "
            "PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 13,
    },
    "validated_evidence_packages": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('VALIDATED_EVIDENCE_PACKAGE') STORED",
        ],
        "check_tuple": [
            "ck_task7_validated_evidence_packages_output_shape(run_output_object_id "
            "IS NULL OR (run_output_object_class='NATIVE_ROW' "
            "AND "
            "run_output_object_kind='VALIDATED_EVIDENCE_PACKAGE'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_validated_evidence_packages_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_validated_evidence_packages_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_validated_evidence_packages_output_root(run_output_object_id)",
            "uq_v2_validated_evidence_package_scope(tenant_id,deal_id,run_id,package_id)",
            "uq_v2_validated_evidence_packages_reference_parent(tenant_id,deal_id,run_id,package_id,run_output_object_id)",
        ],
        "index_tuple": [
            "ix_task7_validated_evidence_packages_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE validated_evidence_packages ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE validated_evidence_packages FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "tenant_isolation_validated_evidence_packages ON "
            "validated_evidence_packages AS PERMISSIVE FOR ALL "
            "TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 13,
    },
    "muhasabah_records": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT NULL",
        ],
        "check_tuple": [
            "ck_task7_muhasabah_records_output_shape((run_output_object_id "
            "IS NULL AND run_output_object_kind IS NULL) OR "
            "(run_output_object_id IS NOT NULL AND "
            "run_output_object_class='NATIVE_ROW' AND "
            "((source_step='METHODOLOGY_EVIDENCE_TRUST_COURT' AND "
            "run_output_object_kind='COURT_MUHASABAH') OR "
            "(source_step='DEBATE' AND "
            "run_output_object_kind='DEBATE_MUHASABAH') OR "
            "(source_step='ANALYSIS' AND agent_id IN "
            "('financial_agent','market_agent','technical_agent','terms_agent','team_agent','risk_officer_agent','historian_agent','sector_specialist_agent') "
            "AND run_output_object_kind='ANALYSIS_MUHASABAH') OR "
            "(source_step='LAYER2_IC_CHALLENGE' AND "
            "run_output_object_kind='LAYER2_MUHASABAH'))))"
        ],
        "foreign_key_tuple": [
            "fk_task7_muhasabah_records_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_muhasabah_records_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_muhasabah_records_output_root(run_output_object_id)",
            "uq_v2_muhasabah_debate_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
            "uq_v2_muhasabah_analysis_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
            "uq_v2_muhasabah_layer2_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
        ],
        "index_tuple": [
            "ix_task7_muhasabah_records_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
            "ix_v2_muhasabah_debate_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
            "ix_v2_muhasabah_analysis_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
            "ix_v2_muhasabah_layer2_parent(tenant_id,deal_id,run_id,record_id,source_step,agent_id,output_id,run_output_object_kind)",
        ],
        "rls_tuple": [
            "ALTER TABLE muhasabah_records ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE muhasabah_records FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_muhasabah_records ON "
            "muhasabah_records AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 14,
    },
    "runs": {
        "column_tuple": [
            "pipeline_contract_version TEXT NOT NULL",
            "pipeline_contract_hash TEXT NOT NULL",
            "pipeline_resume_state TEXT NOT NULL",
            "pipeline_resume_error_code VARCHAR(100) NULL",
        ],
        "check_tuple": [
            "ck_task7_runs_pipeline_contract_version(btrim(pipeline_contract_version)<>'')",
            "ck_task7_runs_pipeline_contract_hash(pipeline_contract_hash~'^[0-9a-f]{64}$')",
            "ck_task7_runs_pipeline_resume_state(pipeline_resume_state IN "
            "('RESUMABLE','HISTORICAL_TERMINAL','NON_RESUMABLE'))",
            "ck_task7_runs_pipeline_resume_error((pipeline_resume_state='NON_RESUMABLE' "
            "AND pipeline_resume_error_code='LEGACY_RUN_RESUME_UNSAFE') OR "
            "(pipeline_resume_state<>'NON_RESUMABLE' AND pipeline_resume_error_code "
            "IS NULL))",
        ],
        "foreign_key_tuple": [
            "fk_task7_runs_deal(tenant_id,deal_id) -> "
            "deals.uq_task7_deals_tenant_deal(tenant_id,deal_id) ON UPDATE NO "
            "ACTION ON DELETE NO ACTION USING uq_task7_deals_tenant_deal"
        ],
        "unique_tuple": [
            "uq_task7_runs_tenant_run(tenant_id,run_id)",
            "uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id)",
        ],
        "index_tuple": ["idx_runs_tenant_id(tenant_id)", "idx_runs_deal_id(deal_id)"],
        "rls_tuple": [
            "ALTER TABLE runs ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE runs FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_runs ON runs AS PERMISSIVE FOR ALL TO "
            "PUBLIC USING (NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) WITH "
            "CHECK (NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 15,
    },
    "deliverables": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('DELIVERABLE_ARTIFACT') STORED",
            "content_type TEXT NULL",
            "size_bytes BIGINT NULL",
            "content_sha256 TEXT NULL",
            "uri_sha256 TEXT NULL",
            "object_version_hash TEXT NULL",
            "download_proof_hash TEXT NULL",
            "catalog_ordinal INTEGER NULL",
        ],
        "check_tuple": [
            "ck_task7_deliverables_terminal_root((run_output_object_id IS "
            "NULL AND content_type IS NULL AND size_bytes IS NULL AND "
            "content_sha256 IS NULL AND uri_sha256 IS NULL AND "
            "object_version_hash IS NULL AND download_proof_hash IS NULL AND "
            "catalog_ordinal IS NULL) OR (run_output_object_id IS NOT NULL "
            "AND run_id IS NOT NULL AND status='COMPLETED' AND "
            "run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='DELIVERABLE_ARTIFACT' AND "
            "btrim(content_type)<>'' AND size_bytes>0 AND "
            "content_sha256~'^[0-9a-f]{64}$' AND uri_sha256~'^[0-9a-f]{64}$' "
            "AND object_version_hash~'^[0-9a-f]{64}$' AND "
            "download_proof_hash~'^[0-9a-f]{64}$' AND catalog_ordinal "
            "BETWEEN 0 AND 14))"
        ],
        "foreign_key_tuple": [
            "fk_task7_deliverables_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_deliverables_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_deliverables_output_root(run_output_object_id)",
            "uq_task7_deliverables_manifest_parent(tenant_id,deal_id,run_id,deliverable_id,run_output_object_id,catalog_ordinal)",
            "uq_v2_deliverables_reference_parent(tenant_id,deal_id,run_id,deliverable_id,run_output_object_id)",
        ],
        "index_tuple": [
            "ix_task7_deliverables_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
            "ix_v2_deliverables_reference_parent(tenant_id,deal_id,run_id,deliverable_id,run_output_object_id)",
        ],
        "rls_tuple": [
            "ALTER TABLE deliverables ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE deliverables FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_deliverables ON deliverables AS "
            "PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL "
            "AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK (NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 16,
    },
    "vector_embeddings": {
        "column_tuple": [
            "producer_step_name VARCHAR(100) NULL",
            "embedding_namespace TEXT NULL",
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT NULL",
        ],
        "check_tuple": [
            "ck_task7_vector_embeddings_output_shape((run_id IS NULL "
            "AND producer_step_name IS NULL AND embedding_namespace IS "
            "NULL AND run_output_object_id IS NULL AND "
            "run_output_object_kind IS NULL) OR (run_id IS NOT NULL AND "
            "run_output_object_id IS NOT NULL AND "
            "run_output_object_class='NATIVE_ROW' AND "
            "((producer_step_name='EXTRACT' AND "
            "embedding_namespace='internal_evidence' AND "
            "run_output_object_kind='EXTRACT_RAG_EMBEDDING') OR "
            "(producer_step_name='RAG_EVIDENCE' AND embedding_namespace "
            "IN ('internal_evidence','external_context') AND "
            "run_output_object_kind='RAG_EVIDENCE_EMBEDDING'))))"
        ],
        "foreign_key_tuple": [
            "fk_task7_vector_embeddings_run(tenant_id,deal_id,run_id) "
            "-> "
            "runs.uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_vector_embeddings_run_fk",
            "fk_task7_vector_embeddings_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_vector_embeddings_output_root_scope",
        ],
        "unique_tuple": [
            "uq_task7_vector_embeddings_output_root(run_output_object_id)",
            "ux_vector_embeddings_reusable(tenant_id,deal_id,source_type,source_id,content_hash,embedding_model,embedding_dimensions) "
            "WHERE run_id IS NULL",
            "ux_vector_embeddings_strict_run(tenant_id,deal_id,run_id,source_type,source_id,content_hash,embedding_model,embedding_dimensions) "
            "WHERE run_id IS NOT NULL",
        ],
        "index_tuple": [
            "ix_task7_vector_embeddings_run_fk(tenant_id,deal_id,run_id) WHERE run_id IS NOT NULL",
            "ix_task7_vector_embeddings_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)",
        ],
        "rls_tuple": [
            "ALTER TABLE vector_embeddings ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE vector_embeddings FORCE ROW LEVEL SECURITY",
            "CREATE POLICY vector_embeddings_tenant_isolation ON "
            "vector_embeddings AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 16,
    },
    "run_output_objects": {
        "column_tuple": [
            "output_object_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "output_class TEXT NOT NULL",
            "output_kind TEXT NOT NULL",
            "output_schema_version TEXT NOT NULL",
            "native_identity_sha256 TEXT NOT NULL",
            "native_output_sha256 TEXT NOT NULL",
            "pipeline_contract_version TEXT NOT NULL",
            "pipeline_contract_hash TEXT NOT NULL",
            "write_origin TEXT NOT NULL",
            "lease_owner_id UUID NULL",
            "lease_fencing_token BIGINT NOT NULL DEFAULT 0",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ],
        "check_tuple": [
            "ck_task7_run_output_objects_class(output_class IN ('STEP_RECEIPT','NATIVE_ROW'))",
            "ck_task7_run_output_objects_origin(write_origin IN ('BACKFILL','RUNTIME'))",
            "ck_task7_run_output_objects_hashes(native_identity_sha256~'^[0-9a-f]{64}$' "
            "AND native_output_sha256~'^[0-9a-f]{64}$' AND "
            "pipeline_contract_hash~'^[0-9a-f]{64}$')",
            "ck_task7_run_output_objects_nonblank(btrim(output_kind)<>'' "
            "AND btrim(output_schema_version)<>'' AND "
            "btrim(pipeline_contract_version)<>'')",
            "ck_task7_run_output_objects_lease((write_origin='BACKFILL' "
            "AND lease_owner_id IS NULL AND lease_fencing_token=0) OR "
            "(write_origin='RUNTIME' AND lease_owner_id IS NOT NULL "
            "AND lease_fencing_token>0))",
        ],
        "foreign_key_tuple": [
            "fk_task7_run_output_objects_run(tenant_id,deal_id,run_id) "
            "-> "
            "runs.uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_run_output_objects_kind"
        ],
        "unique_tuple": [
            "pk_task7_run_output_objects(output_object_id)",
            "uq_task7_run_output_objects_scope(tenant_id,deal_id,run_id,output_object_id)",
            "uq_task7_run_output_objects_tenant_run_id(tenant_id,run_id,output_object_id)",
            "uq_task7_run_output_objects_step_ref(tenant_id,deal_id,run_id,output_object_id,output_kind)",
            "uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind)",
            "uq_task7_run_output_objects_semantic_identity(tenant_id,run_id,output_kind,native_identity_sha256)",
        ],
        "index_tuple": [
            "ix_task7_run_output_objects_kind(tenant_id,deal_id,run_id,output_kind)",
            "ix_task7_run_output_objects_created(tenant_id,run_id,created_at,output_object_id)",
            "ix_task7_run_output_objects_output_sha(tenant_id,run_id,output_kind,native_output_sha256)",
        ],
        "rls_tuple": [
            "ALTER TABLE run_output_objects ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE run_output_objects FORCE ROW LEVEL SECURITY",
            "CREATE POLICY run_output_objects_tenant_isolation ON "
            "run_output_objects AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
            "REVOKE ALL PRIVILEGES ON TABLE run_output_objects FROM PUBLIC",
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "
            "run_output_objects TO <configured_app_role>",
        ],
        "erasure_ordinal": 17,
    },
    "run_input_bindings": {
        "column_tuple": [
            "binding_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "run_id UUID NOT NULL",
            "source_lineage_object_id UUID NOT NULL",
            "source_object_kind TEXT NOT NULL",
            "source_disposition TEXT NOT NULL",
            "binding_purpose TEXT NOT NULL",
            "corpus_receipt_id UUID NOT NULL",
            "selected_source_version TEXT NOT NULL",
            "selected_identity_sha256 TEXT NOT NULL",
            "selected_content_sha256 TEXT NOT NULL",
            "corpus_receipt_sha256 TEXT NOT NULL",
            "pipeline_contract_version TEXT NOT NULL",
            "pipeline_contract_hash TEXT NOT NULL",
            "binding_sha256 TEXT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ],
        "check_tuple": [
            "ck_task7_run_input_bindings_source_kind(source_object_kind "
            "IN "
            "('PACKAGE','PACKAGE_FILE','ARTIFACT','DOCUMENT','PARSE_ATTEMPT','SPAN'))",
            "ck_task7_run_input_bindings_disposition(source_disposition "
            "IN ('CORPUS','EVIDENCE_BEARING','CONFIRMED_EXCLUSION'))",
            "ck_task7_run_input_bindings_purpose(binding_purpose IN "
            "('INVENTORY','HANDOFF','EVIDENCE'))",
            "ck_task7_run_input_bindings_hashes(selected_identity_sha256~'^[0-9a-f]{64}$' "
            "AND selected_content_sha256~'^[0-9a-f]{64}$' AND "
            "corpus_receipt_sha256~'^[0-9a-f]{64}$' AND "
            "pipeline_contract_hash~'^[0-9a-f]{64}$' AND "
            "binding_sha256~'^[0-9a-f]{64}$')",
            "ck_task7_run_input_bindings_nonblank(btrim(selected_source_version)<>'' "
            "AND btrim(pipeline_contract_version)<>'')",
            "ck_task7_run_input_bindings_recipe((source_object_kind='PACKAGE' "
            "AND source_disposition='CORPUS' AND "
            "binding_purpose='INVENTORY') OR "
            "(source_object_kind='PACKAGE_FILE' AND source_disposition "
            "IN ('EVIDENCE_BEARING','CONFIRMED_EXCLUSION') AND "
            "binding_purpose='INVENTORY') OR (source_object_kind IN "
            "('ARTIFACT','DOCUMENT','PARSE_ATTEMPT') AND "
            "source_disposition IN "
            "('EVIDENCE_BEARING','CONFIRMED_EXCLUSION') AND "
            "binding_purpose='HANDOFF') OR (source_object_kind='SPAN' "
            "AND source_disposition='EVIDENCE_BEARING' AND "
            "binding_purpose='EVIDENCE'))",
        ],
        "foreign_key_tuple": [
            "fk_task7_run_input_bindings_run(tenant_id,deal_id,run_id) "
            "-> "
            "runs.uq_task7_runs_tenant_deal_run(tenant_id,deal_id,run_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_run_input_bindings_run_fk",
            "fk_task7_run_input_bindings_corpus(tenant_id,deal_id,corpus_receipt_id) "
            "-> "
            "data_room_packages.uq_task7_data_room_packages_scope(tenant_id,deal_id,package_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_run_input_bindings_corpus",
            "fk_task7_run_input_bindings_source(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,source_object_kind,selected_source_version,source_disposition,selected_identity_sha256,selected_content_sha256,corpus_receipt_sha256) "
            "-> "
            "source_lineage_objects.uq_task7_source_lineage_objects_binding_parent(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind,source_version,source_disposition,object_identity_sha256,content_sha256,corpus_receipt_sha256) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_run_input_bindings_source_fk",
        ],
        "unique_tuple": [
            "pk_task7_run_input_bindings(binding_id)",
            "uq_task7_run_input_bindings_run_source(tenant_id,run_id,source_lineage_object_id)",
            "uq_task7_run_input_bindings_scoped_id(tenant_id,deal_id,run_id,binding_id)",
            "uq_task7_run_input_bindings_binding_sha(tenant_id,run_id,binding_sha256)",
        ],
        "index_tuple": [
            "ix_task7_run_input_bindings_run_fk(tenant_id,deal_id,run_id)",
            "ix_task7_run_input_bindings_corpus(tenant_id,deal_id,corpus_receipt_id)",
            "ix_task7_run_input_bindings_source_fk(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,source_object_kind,selected_source_version,source_disposition,selected_identity_sha256,selected_content_sha256,corpus_receipt_sha256)",
        ],
        "rls_tuple": [
            "ALTER TABLE run_input_bindings ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE run_input_bindings FORCE ROW LEVEL SECURITY",
            "CREATE POLICY run_input_bindings_tenant_isolation ON "
            "run_input_bindings AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
            "REVOKE ALL PRIVILEGES ON TABLE run_input_bindings FROM PUBLIC",
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "
            "run_input_bindings TO <configured_app_role>",
        ],
        "erasure_ordinal": 18,
    },
    "document_spans": {
        "column_tuple": [
            "deal_id UUID NOT NULL",
            "parse_attempt_id UUID NULL",
            "source_binding_state TEXT NOT NULL",
            "source_lineage_object_id UUID NULL",
            "source_lineage_object_kind TEXT GENERATED ALWAYS AS ('SPAN') STORED",
            "source_corpus_receipt_id UUID NULL",
        ],
        "check_tuple": [
            "ck_task7_document_spans_binding_state((source_binding_state='UNBOUND' "
            "AND source_lineage_object_id IS NULL AND "
            "source_corpus_receipt_id IS NULL AND parse_attempt_id IS "
            "NULL) OR (source_binding_state='BOUND' AND "
            "source_lineage_object_id IS NOT NULL AND "
            "source_corpus_receipt_id IS NOT NULL AND parse_attempt_id IS "
            "NOT NULL))"
        ],
        "foreign_key_tuple": [
            "fk_task7_document_spans_source_root(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind) "
            "-> "
            "source_lineage_objects.uq_task7_source_lineage_objects_corpus_scope_kind(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_document_spans_source_root_scope",
            "fk_task7_spans_document(tenant_id,deal_id,document_id) "
            "-> "
            "documents.uq_task7_documents_scope(tenant_id,deal_id,document_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_spans_document_fk",
            "fk_task7_spans_deal(tenant_id,deal_id) -> "
            "deals.uq_task7_deals_tenant_deal(tenant_id,deal_id) ON "
            "UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_spans_deal_fk",
            "fk_task7_spans_parse_attempt(tenant_id,deal_id,document_id,parse_attempt_id) "
            "-> "
            "document_parse_attempts.uq_task7_document_parse_attempts_scope(tenant_id,deal_id,document_id,parse_attempt_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_spans_parse_attempt_fk",
        ],
        "unique_tuple": [
            "ux_document_spans_task6_scope(tenant_id,deal_id,document_id,span_id)",
            "uq_task7_document_spans_source_root(source_lineage_object_id)",
        ],
        "index_tuple": [
            "ix_task7_document_spans_source_root_scope(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind)",
            "ix_task7_spans_document_fk(tenant_id,deal_id,document_id)",
            "ix_task7_spans_deal_fk(tenant_id,deal_id)",
            "ix_task7_spans_parse_attempt_fk(tenant_id,deal_id,document_id,parse_attempt_id) "
            "WHERE parse_attempt_id IS NOT NULL",
        ],
        "rls_tuple": [
            "ALTER TABLE document_spans ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE document_spans FORCE ROW LEVEL SECURITY",
            "CREATE POLICY document_spans_tenant_isolation ON document_spans "
            "AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL "
            "AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK (NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 19,
    },
    "data_room_package_files": {
        "column_tuple": [
            "source_binding_state TEXT NOT NULL",
            "source_lineage_object_id UUID NULL",
            "source_lineage_object_kind TEXT GENERATED ALWAYS AS ('PACKAGE_FILE') STORED",
            "source_corpus_receipt_id UUID GENERATED ALWAYS AS (package_id) STORED",
        ],
        "check_tuple": [
            "ck_task7_data_room_package_files_binding_state((source_binding_state='UNBOUND' "
            "AND source_lineage_object_id IS NULL) OR "
            "(source_binding_state='BOUND' AND "
            "source_lineage_object_id IS NOT NULL))",
            "ck_task7_package_files_document_pair((source_binding_state='UNBOUND' "
            "AND doc_id IS NULL AND document_id IS NULL) OR "
            "(source_binding_state='BOUND' AND doc_id IS NOT NULL "
            "AND document_id IS NOT NULL))",
        ],
        "foreign_key_tuple": [
            "fk_task7_data_room_package_files_source_root(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind) "
            "-> "
            "source_lineage_objects.uq_task7_source_lineage_objects_corpus_scope_kind(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_data_room_package_files_source_root_scope",
            "fk_task7_package_files_package(tenant_id,deal_id,package_id) "
            "-> "
            "data_room_packages.uq_task7_data_room_packages_scope(tenant_id,deal_id,package_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_package_files_package_fk",
            "fk_task7_package_files_document(tenant_id,deal_id,doc_id,document_id) "
            "-> "
            "documents.uq_task7_documents_artifact_document(tenant_id,deal_id,doc_id,document_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_package_files_document_fk",
        ],
        "unique_tuple": [
            "uq_task7_data_room_package_files_source_root(source_lineage_object_id)",
            "ux_task7_package_files_doc_id_bound(tenant_id,deal_id,doc_id) "
            "WHERE doc_id IS NOT NULL",
            "ux_task7_package_files_document_id_bound(tenant_id,deal_id,document_id) "
            "WHERE document_id IS NOT NULL",
        ],
        "index_tuple": [
            "ix_task7_data_room_package_files_source_root_scope(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind)",
            "ix_task7_package_files_package_fk(tenant_id,deal_id,package_id)",
            "ix_task7_package_files_document_fk(tenant_id,deal_id,doc_id,document_id)",
        ],
        "rls_tuple": [
            "ALTER TABLE data_room_package_files ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE data_room_package_files FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_data_room_package_files "
            "ON data_room_package_files AS PERMISSIVE FOR ALL TO "
            "PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 20,
    },
    "document_parse_attempts": {
        "column_tuple": [
            "parse_attempt_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "document_id UUID NOT NULL",
            "attempt_ordinal INTEGER NOT NULL",
            "parser_name TEXT NOT NULL",
            "parser_version TEXT NOT NULL",
            "input_sha256 TEXT NOT NULL",
            "receipt_sha256 TEXT NOT NULL",
            "status TEXT NOT NULL",
            "started_at TIMESTAMPTZ NOT NULL",
            "finished_at TIMESTAMPTZ NOT NULL",
            "source_binding_state TEXT NOT NULL",
            "source_lineage_object_id UUID NULL",
            "source_lineage_object_kind TEXT GENERATED ALWAYS AS ('PARSE_ATTEMPT') STORED",
            "source_corpus_receipt_id UUID NULL",
            "write_origin TEXT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ],
        "check_tuple": [
            "ck_task7_document_parse_attempts_ordinal(attempt_ordinal>0)",
            "ck_task7_document_parse_attempts_status(status IN ('PARSED','FAILED'))",
            "ck_task7_document_parse_attempts_times(finished_at>=started_at)",
            "ck_task7_document_parse_attempts_origin(write_origin IN ('BACKFILL','RUNTIME'))",
            "ck_task7_document_parse_attempts_hashes(input_sha256~'^[0-9a-f]{64}$' "
            "AND receipt_sha256~'^[0-9a-f]{64}$')",
            "ck_task7_document_parse_attempts_nonblank(btrim(parser_name)<>'' "
            "AND btrim(parser_version)<>'')",
            "ck_task7_document_parse_attempts_binding_state((source_binding_state='UNBOUND' "
            "AND source_lineage_object_id IS NULL AND "
            "source_corpus_receipt_id IS NULL) OR "
            "(source_binding_state='BOUND' AND "
            "source_lineage_object_id IS NOT NULL AND "
            "source_corpus_receipt_id IS NOT NULL))",
        ],
        "foreign_key_tuple": [
            "fk_task7_parse_attempts_document(tenant_id,deal_id,document_id) "
            "-> "
            "documents.uq_task7_documents_scope(tenant_id,deal_id,document_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_document_parse_attempts_document_ordinal",
            "fk_task7_document_parse_attempts_source_root(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind) "
            "-> "
            "source_lineage_objects.uq_task7_source_lineage_objects_corpus_scope_kind(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_document_parse_attempts_source_root_scope",
        ],
        "unique_tuple": [
            "pk_task7_document_parse_attempts(parse_attempt_id)",
            "uq_task7_document_parse_attempts_ordinal(tenant_id,deal_id,document_id,attempt_ordinal)",
            "uq_task7_document_parse_attempts_scope(tenant_id,deal_id,document_id,parse_attempt_id)",
            "uq_task7_document_parse_attempts_source_root(source_lineage_object_id)",
        ],
        "index_tuple": [
            "ix_task7_document_parse_attempts_document_ordinal(tenant_id,deal_id,document_id,attempt_ordinal "
            "DESC,parse_attempt_id)",
            "ix_task7_document_parse_attempts_receipt(tenant_id,deal_id,receipt_sha256)",
            "ix_task7_document_parse_attempts_source_root_scope(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind)",
        ],
        "rls_tuple": [
            "ALTER TABLE document_parse_attempts ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE document_parse_attempts FORCE ROW LEVEL SECURITY",
            "CREATE POLICY document_parse_attempts_tenant_isolation "
            "ON document_parse_attempts AS PERMISSIVE FOR ALL TO "
            "PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
            "REVOKE ALL PRIVILEGES ON TABLE document_parse_attempts FROM PUBLIC",
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "
            "document_parse_attempts TO <configured_app_role>",
        ],
        "erasure_ordinal": 20,
    },
    "documents": {
        "column_tuple": [
            "source_binding_state TEXT NOT NULL",
            "source_lineage_object_id UUID NULL",
            "source_lineage_object_kind TEXT GENERATED ALWAYS AS ('DOCUMENT') STORED",
            "source_corpus_receipt_id UUID NULL",
        ],
        "check_tuple": [
            "ck_task7_documents_binding_state((source_binding_state='UNBOUND' "
            "AND source_lineage_object_id IS NULL AND source_corpus_receipt_id "
            "IS NULL) OR (source_binding_state='BOUND' AND "
            "source_lineage_object_id IS NOT NULL AND source_corpus_receipt_id "
            "IS NOT NULL))"
        ],
        "foreign_key_tuple": [
            "fk_task7_documents_source_root(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind) "
            "-> "
            "source_lineage_objects.uq_task7_source_lineage_objects_corpus_scope_kind(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_documents_source_root_scope",
            "fk_task7_documents_artifact(tenant_id,deal_id,doc_id) -> "
            "document_artifacts.uq_task7_document_artifacts_scope(tenant_id,deal_id,doc_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_documents_artifact_fk",
        ],
        "unique_tuple": [
            "uq_task7_documents_scope(tenant_id,deal_id,document_id)",
            "uq_task7_documents_artifact_document(tenant_id,deal_id,doc_id,document_id)",
            "uq_task7_documents_source_root(source_lineage_object_id)",
        ],
        "index_tuple": [
            "ix_task7_documents_source_root_scope(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind)",
            "ix_task7_documents_artifact_fk(tenant_id,deal_id,doc_id)",
        ],
        "rls_tuple": [
            "ALTER TABLE documents ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE documents FORCE ROW LEVEL SECURITY",
            "CREATE POLICY documents_tenant_isolation ON documents AS PERMISSIVE "
            "FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK (NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 21,
    },
    "document_artifacts": {
        "column_tuple": [
            "source_binding_state TEXT NOT NULL",
            "source_lineage_object_id UUID NULL",
            "source_lineage_object_kind TEXT GENERATED ALWAYS AS ('ARTIFACT') STORED",
            "source_corpus_receipt_id UUID NULL",
        ],
        "check_tuple": [
            "ck_task7_document_artifacts_binding_state((source_binding_state='UNBOUND' "
            "AND source_lineage_object_id IS NULL AND "
            "source_corpus_receipt_id IS NULL) OR "
            "(source_binding_state='BOUND' AND "
            "source_lineage_object_id IS NOT NULL AND "
            "source_corpus_receipt_id IS NOT NULL))"
        ],
        "foreign_key_tuple": [
            "fk_task7_document_artifacts_source_root(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind) "
            "-> "
            "source_lineage_objects.uq_task7_source_lineage_objects_corpus_scope_kind(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_document_artifacts_source_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_document_artifacts_scope(tenant_id,deal_id,doc_id)",
            "uq_task7_document_artifacts_source_root(source_lineage_object_id)",
        ],
        "index_tuple": [
            "ix_task7_document_artifacts_source_root_scope(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE document_artifacts ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE document_artifacts FORCE ROW LEVEL SECURITY",
            "CREATE POLICY document_artifacts_tenant_isolation ON "
            "document_artifacts AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 22,
    },
    "data_room_packages": {
        "column_tuple": [
            "source_binding_state TEXT NOT NULL",
            "source_lineage_object_id UUID NULL",
            "source_lineage_object_kind TEXT GENERATED ALWAYS AS ('PACKAGE') STORED",
            "source_corpus_receipt_id UUID GENERATED ALWAYS AS (package_id) STORED",
        ],
        "check_tuple": [
            "ck_task7_data_room_packages_binding_state((status='OPEN' "
            "AND source_binding_state='UNBOUND' AND "
            "source_lineage_object_id IS NULL) OR (status='SEALED' AND "
            "source_binding_state='BOUND' AND source_lineage_object_id "
            "IS NOT NULL))"
        ],
        "foreign_key_tuple": [
            "fk_task7_data_room_packages_source_root(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind) "
            "-> "
            "source_lineage_objects.uq_task7_source_lineage_objects_corpus_scope_kind(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_data_room_packages_source_root_scope "
            "DEFERRABLE INITIALLY DEFERRED"
        ],
        "unique_tuple": [
            "uq_task7_data_room_packages_scope(tenant_id,deal_id,package_id)",
            "uq_task7_data_room_packages_source_root(source_lineage_object_id)",
        ],
        "index_tuple": [
            "ix_task7_data_room_packages_source_root_scope(tenant_id,deal_id,source_corpus_receipt_id,source_lineage_object_id,source_lineage_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE data_room_packages ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE data_room_packages FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_data_room_packages ON "
            "data_room_packages AS PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 23,
    },
    "source_lineage_objects": {
        "column_tuple": [
            "source_lineage_object_id UUID NOT NULL",
            "tenant_id UUID NOT NULL",
            "deal_id UUID NOT NULL",
            "corpus_receipt_id UUID NOT NULL",
            "object_kind TEXT NOT NULL",
            "source_version TEXT NOT NULL",
            "source_disposition TEXT NOT NULL",
            "object_identity_sha256 TEXT NOT NULL",
            "content_sha256 TEXT NOT NULL",
            "corpus_receipt_sha256 TEXT NOT NULL",
            "write_origin TEXT NOT NULL",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()",
        ],
        "check_tuple": [
            "ck_task7_source_lineage_objects_kind(object_kind IN "
            "('PACKAGE','PACKAGE_FILE','ARTIFACT','DOCUMENT','PARSE_ATTEMPT','SPAN'))",
            "ck_task7_source_lineage_objects_disposition(source_disposition "
            "IN "
            "('CORPUS','EVIDENCE_BEARING','CONFIRMED_EXCLUSION'))",
            "ck_task7_source_lineage_objects_origin(write_origin IN ('BACKFILL','RUNTIME'))",
            "ck_task7_source_lineage_objects_hashes(object_identity_sha256~'^[0-9a-f]{64}$' "
            "AND content_sha256~'^[0-9a-f]{64}$' AND "
            "corpus_receipt_sha256~'^[0-9a-f]{64}$')",
            "ck_task7_source_lineage_objects_nonblank(btrim(source_version)<>'')",
        ],
        "foreign_key_tuple": [
            "fk_task7_source_lineage_objects_deal(tenant_id,deal_id) "
            "-> "
            "deals.uq_task7_deals_tenant_deal(tenant_id,deal_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "uq_task7_deals_tenant_deal",
            "fk_task7_source_lineage_objects_corpus(tenant_id,deal_id,corpus_receipt_id) "
            "-> "
            "data_room_packages.uq_task7_data_room_packages_scope(tenant_id,deal_id,package_id) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION USING "
            "ix_task7_source_lineage_objects_corpus_kind "
            "DEFERRABLE INITIALLY DEFERRED",
        ],
        "unique_tuple": [
            "pk_task7_source_lineage_objects(source_lineage_object_id)",
            "uq_task7_source_lineage_objects_scope_kind(tenant_id,deal_id,source_lineage_object_id,object_kind)",
            "uq_task7_source_lineage_objects_corpus_scope_kind(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind)",
            "uq_task7_source_lineage_objects_binding_parent(tenant_id,deal_id,corpus_receipt_id,source_lineage_object_id,object_kind,source_version,source_disposition,object_identity_sha256,content_sha256,corpus_receipt_sha256)",
            "uq_task7_source_lineage_objects_identity_version(tenant_id,deal_id,corpus_receipt_id,object_kind,object_identity_sha256,source_version)",
        ],
        "index_tuple": [
            "ix_task7_source_lineage_objects_corpus_kind(tenant_id,deal_id,corpus_receipt_id,object_kind)",
            "ix_task7_source_lineage_objects_content(tenant_id,deal_id,content_sha256)",
        ],
        "rls_tuple": [
            "ALTER TABLE source_lineage_objects ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE source_lineage_objects FORCE ROW LEVEL SECURITY",
            "CREATE POLICY source_lineage_objects_tenant_isolation "
            "ON source_lineage_objects AS PERMISSIVE FOR ALL TO "
            "PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS "
            "NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
            "REVOKE ALL PRIVILEGES ON TABLE source_lineage_objects FROM PUBLIC",
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "
            "source_lineage_objects TO <configured_app_role>",
        ],
        "erasure_ordinal": 24,
    },
    "run_steps": {
        "column_tuple": [
            "pipeline_contract_version TEXT NOT NULL",
            "pipeline_contract_hash TEXT NOT NULL",
            "semantic_rule_version TEXT NOT NULL",
            "semantic_rule_hash TEXT NOT NULL",
        ],
        "check_tuple": [
            "ck_task7_run_steps_order(step_order>=0)",
            "ck_task7_run_steps_pipeline_contract_version(btrim(pipeline_contract_version)<>'')",
            "ck_task7_run_steps_pipeline_contract_hash(pipeline_contract_hash~'^[0-9a-f]{64}$')",
            "ck_task7_run_steps_semantic_rule_version(btrim(semantic_rule_version)<>'')",
            "ck_task7_run_steps_semantic_rule_hash(semantic_rule_hash~'^[0-9a-f]{64}$')",
        ],
        "foreign_key_tuple": [
            "fk_task7_run_steps_run(tenant_id,run_id) -> "
            "runs.uq_task7_runs_tenant_run(tenant_id,run_id) ON UPDATE NO "
            "ACTION ON DELETE NO ACTION USING "
            "uq_task7_run_steps_tenant_run_name"
        ],
        "unique_tuple": [
            "uq_task7_run_steps_order(tenant_id,run_id,step_order)",
            "uq_task7_run_steps_tenant_run_name(tenant_id,run_id,step_name)",
            "uq_task7_run_steps_scoped_id_name(tenant_id,run_id,step_id,step_name)",
        ],
        "index_tuple": [
            "idx_run_steps_tenant_run(tenant_id,run_id)",
            "idx_run_steps_tenant_run_order(tenant_id,run_id,step_order)",
        ],
        "rls_tuple": [
            "ALTER TABLE run_steps ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE run_steps FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_run_steps ON run_steps AS PERMISSIVE "
            "FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK (NULLIF(current_setting('idis.tenant_id',true),'') IS NOT "
            "NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 25,
    },
    "methodology_result_evidence_links": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('METHODOLOGY_EVIDENCE_LINK') STORED",
        ],
        "check_tuple": [
            "ck_task7_methodology_result_evidence_links_output_shape(run_output_object_id "
            "IS NULL OR "
            "(run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='METHODOLOGY_EVIDENCE_LINK'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_methodology_result_evidence_links_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO "
            "ACTION USING "
            "ix_task7_methodology_result_evidence_links_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_methodology_result_evidence_links_output_root(run_output_object_id)"
        ],
        "index_tuple": [
            "ix_task7_methodology_result_evidence_links_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE methodology_result_evidence_links ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE methodology_result_evidence_links FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "methodology_result_evidence_links_tenant_isolation "
            "ON methodology_result_evidence_links AS "
            "PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 63,
    },
    "methodology_result_sanad_links": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('METHODOLOGY_SANAD_LINK') STORED",
        ],
        "check_tuple": [
            "ck_task7_methodology_result_sanad_links_output_shape(run_output_object_id "
            "IS NULL OR "
            "(run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='METHODOLOGY_SANAD_LINK'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_methodology_result_sanad_links_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO ACTION "
            "USING "
            "ix_task7_methodology_result_sanad_links_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_methodology_result_sanad_links_output_root(run_output_object_id)",
            "uq_v2_methodology_sanad_grading_parent(tenant_id,deal_id,run_id,sanad_link_id,sanad_id)",
        ],
        "index_tuple": [
            "ix_task7_methodology_result_sanad_links_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE methodology_result_sanad_links ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE methodology_result_sanad_links FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "methodology_result_sanad_links_tenant_isolation "
            "ON methodology_result_sanad_links AS PERMISSIVE "
            "FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 63,
    },
    "methodology_result_defect_links": {
        "column_tuple": [
            "run_output_object_id UUID NULL",
            "run_output_object_class TEXT GENERATED ALWAYS AS ('NATIVE_ROW') STORED",
            "run_output_object_kind TEXT GENERATED ALWAYS AS ('METHODOLOGY_DEFECT_LINK') STORED",
        ],
        "check_tuple": [
            "ck_task7_methodology_result_defect_links_output_shape(run_output_object_id "
            "IS NULL OR "
            "(run_output_object_class='NATIVE_ROW' AND "
            "run_output_object_kind='METHODOLOGY_DEFECT_LINK'))"
        ],
        "foreign_key_tuple": [
            "fk_task7_methodology_result_defect_links_output_root(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind) "
            "-> "
            "run_output_objects.uq_task7_run_output_objects_class_kind(tenant_id,deal_id,run_id,output_object_id,output_class,output_kind) "
            "ON UPDATE NO ACTION ON DELETE NO "
            "ACTION USING "
            "ix_task7_methodology_result_defect_links_output_root_scope"
        ],
        "unique_tuple": [
            "uq_task7_methodology_result_defect_links_output_root(run_output_object_id)"
        ],
        "index_tuple": [
            "ix_task7_methodology_result_defect_links_output_root_scope(tenant_id,deal_id,run_id,run_output_object_id,run_output_object_class,run_output_object_kind)"
        ],
        "rls_tuple": [
            "ALTER TABLE methodology_result_defect_links ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE methodology_result_defect_links FORCE ROW LEVEL SECURITY",
            "CREATE POLICY "
            "methodology_result_defect_links_tenant_isolation "
            "ON methodology_result_defect_links AS "
            "PERMISSIVE FOR ALL TO PUBLIC USING "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid) "
            "WITH CHECK "
            "(NULLIF(current_setting('idis.tenant_id',true),'') "
            "IS NOT NULL AND "
            "tenant_id=NULLIF(current_setting('idis.tenant_id',true),'')::uuid)",
        ],
        "erasure_ordinal": 64,
    },
}

_CURRENT_ERASURE_RELATIONS_V1: Final = (
    "deliverable_manifest_references",
    "run_consumer_receipts",
    "run_expected_consumer_edges",
    "methodology_result_defect_links",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "methodology_result_claim_links",
    "run_applicability_decisions",
    "run_step_outputs",
    "methodology_result_factual_outputs",
    "methodology_extraction_results",
    "calc_sanads",
    "deterministic_calculations",
    "layer2_ic_findings",
    "layer2_ic_challenges",
    "debate_sessions",
    "evidence_trust_findings",
    "validated_evidence_packages",
    "muhasabah_records",
    "deliverables",
    "vector_embeddings",
    "run_input_bindings",
    "data_room_package_files",
    "break_glass_grants",
    "deal_assignments",
    "defects",
    "human_gate_actions",
    "human_gates",
    "methodology_extraction_attempts",
    "run_steps",
    "methodology_extraction_task_spans",
    "methodology_extraction_tasks",
    "methodology_coverage_records",
    "run_output_objects",
    "document_spans",
    "document_parse_attempts",
    "documents",
    "document_artifacts",
    "data_room_packages",
    "source_lineage_objects",
    "overrides",
    "run_methodology_selections",
    "runs",
    "sanads",
    "evidence_items",
    "claims",
    "deals",
    "strict_preflight_proofs",
)

_CURRENT_ERASURE_BODY_SQL_V1: Final = (
    (
        "CREATE FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ DECLARE deletion_counts JSONB:='{}'::jsonb; deleted_count BIGINT; request_status TEXT; staged_counts JSONB; staged_object_rows BIGINT; artifact_row_count BIGINT; object_target_set_sha256 TEXT; lineage_events JSONB; attempted_matches JSONB; attempted_event JSONB; attempted_count BIGINT; attempt_id UUID; attempt_ordinal INTEGER; prior_status TEXT; prior_event_id UUID; prior_event_hash TEXT; attempted_actor_id TEXT; attempt_name TEXT; event_name TEXT; expected_attempt_id UUID; expected_event_id UUID; prior_event JSONB; prior_matches JSONB; prior_count BIGINT; prior_attempt_id UUID; prior_attempt_name TEXT; expected_prior_attempt_id UUID; prior_event_name TEXT; expected_prior_event_id UUID; canonical_prior_event TEXT; persisted_status TEXT; persisted_actor TEXT; persisted_at TIMESTAMPTZ; persisted_counts JSONB; BEGIN IF NOT ( session_user='idis_erasure_executor' AND current_user='idis_erasure_function_owner' ) THEN RAISE EXCEPTION 'TASK7_ERASURE_EXECUTOR_UNAUTHORIZED' USING ERRCODE='42501'; END IF; PERFORM pg_catalog.set_config('idis.tenant_id',$2::text,true); PERFORM pg_catalog.pg_advisory_xact_lock( pg_catalog.hashtextextended( 'idis-task7-erasure-tenant-lock-v1:'||$2::text,0 ) ); SELECT r.status,r.counts INTO STRICT request_status,staged_counts FROM public.erasure_requests r WHERE r.request_id=$1 AND r.tenant_id=$2 AND r.deal_id=$3 FOR UPDATE; IF request_status NOT IN ('REQUESTED','FAILED') THEN RAISE EXCEPTION 'TASK7_ERASURE_REQUEST_NOT_EXECUTABLE' USING ERRCODE='42501'; END IF; IF pg_catalog.jsonb_typeof(staged_counts)<>'object' OR pg_catalog.jsonb_typeof(staged_counts->'object_rows_reconciled')<>'number' OR (staged_counts->>'object_rows_reconciled')!~'^(0|[1-9][0-9]*)$' OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(staged_counts))<>1 THEN RAISE EXCEPTION 'TASK7_ERASURE_OBJECT_RECONCILIATION_INVA"
        "LID' USING ERRCODE='23514'; END IF; staged_object_rows:=(staged_counts->>'object_rows_reconciled')::bigint; IF EXISTS ( SELECT 1 FROM public.document_artifacts a WHERE a.tenant_id=$2 AND a.deal_id=$3 AND (a.uri IS NULL OR a.uri NOT LIKE 'idis://%' OR a.uri='idis://') ) THEN RAISE EXCEPTION 'TASK7_ERASURE_ARTIFACT_LOCATOR_INVALID' USING ERRCODE='23514'; END IF; SELECT count(*), pg_catalog.encode( pg_catalog.sha256( pg_catalog.convert_to( '[\"task7-erasure-object-target-set-v1\",'|| pg_catalog.to_json($2::text)::text||','|| pg_catalog.to_json($3::text)::text||','|| count(*)::text||',['|| COALESCE( pg_catalog.string_agg( pg_catalog.to_json( pg_catalog.encode( pg_catalog.sha256( pg_catalog.convert_to(a.uri,'UTF8') ), 'hex' ) )::text, ',' ORDER BY a.uri COLLATE \"C\" ), '' )||']]', 'UTF8' ) ), 'hex' ) INTO artifact_row_count,object_target_set_sha256 FROM public.document_artifacts a WHERE a.tenant_id=$2 AND a.deal_id=$3; IF staged_object_rows<>artifact_row_count THEN RAISE EXCEPTION 'TASK7_ERASURE_OBJECT_RECONCILIATION_INVALID' USING ERRCODE='23514'; END IF; SELECT COALESCE(pg_catalog.jsonb_agg(e.event ORDER BY e.event_id),'[]'::jsonb) INTO lineage_events FROM public.audit_events e WHERE e.tenant_id=$2 AND e.event_type IN ( 'task7.erasure.attempted', 'task7.erasure.failed', 'task7.erasure.succeeded' ) AND e.event->>'event_id'=e.event_id::text AND e.event->>'tenant_id'=$2::text AND e.event->'resource'->>'deal_id'=$3::text AND e.event->'payload'->'safe'->>'request_id'=$1::text; SELECT count(*),COALESCE(pg_catalog.jsonb_agg(item),'[]'::jsonb) INTO attempted_count,attempted_matches FROM pg_catalog.jsonb_array_elements(lineage_events) AS events(item) WHERE item->>'event_type'='task7.erasure.attempted' AND item->'payload'->'safe'->>'outcome'='ATTEMPTED' AND item->'actor'->'roles' @> '[\"ADMIN\"]'::jsonb AND item->'payload'->'hashes'= pg_catalog.jsonb_build_array( 'object_target_set_sha256:'||object_target_set_sha256 ) AND NOT EXISTS ( SELECT 1 FROM pg_catalog.jsonb_array_elements(lin"
        "eage_events) AS terminals(terminal) WHERE terminal->'payload'->'safe'->>'attempt_id'= item->'payload'->'safe'->>'attempt_id' AND terminal->>'event_type' IN ( 'task7.erasure.failed','task7.erasure.succeeded' ) ); IF attempted_count<>1 THEN RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPTED_EVENT_INVALID' USING ERRCODE='23514'; END IF; attempted_event:=attempted_matches->0; attempt_id:=(attempted_event->'payload'->'safe'->>'attempt_id')::uuid; attempt_ordinal:=(attempted_event->'payload'->'safe'->>'attempt_ordinal')::integer; prior_status:=attempted_event->'payload'->'safe'->>'prior_status'; prior_event_id:=(attempted_event->'payload'->'safe'->>'prior_attempt_event_id')::uuid; prior_event_hash:=attempted_event->'payload'->'safe'->>'prior_attempt_event_hash'; attempted_actor_id:=attempted_event->'actor'->>'actor_id'; IF attempted_actor_id IS NULL OR btrim(attempted_actor_id)='' THEN RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPTED_EVENT_INVALID' USING ERRCODE='23514'; END IF; attempt_name:= '['||pg_catalog.to_json('task7-erasure-attempt-v1'::text)::text||','|| pg_catalog.to_json($2::text)::text||','|| pg_catalog.to_json($3::text)::text||','|| pg_catalog.to_json($1::text)::text||','|| attempt_ordinal::text||','|| COALESCE(pg_catalog.to_json(prior_status)::text,'null')||','|| COALESCE(pg_catalog.to_json(prior_event_id::text)::text,'null')||','|| COALESCE(pg_catalog.to_json(prior_event_hash)::text,'null')||']'; DECLARE payload bytea; original_octets bigint; zero_count integer; block_offset integer; word_ordinal integer; round_ordinal integer; words bigint[]:=pg_catalog.array_fill(0::bigint,ARRAY[80],ARRAY[0]); h0 bigint:=1732584193; h1 bigint:=4023233417; h2 bigint:=2562383102; h3 bigint:=271733878; h4 bigint:=3285377520; a bigint; b bigint; c bigint; d bigint; e bigint; f bigint; k bigint; temporary bigint; mask constant bigint:=4294967295; length_hex text; digest bytea; value_hex text; BEGIN payload:=pg_catalog.decode( replace('4520d8ed-e929-5486-821c-82f8901ae7fa'::uuid::text,'-',''),'h"
        "ex' )||pg_catalog.convert_to(attempt_name,'UTF8'); original_octets:=pg_catalog.octet_length(payload); payload:=payload||pg_catalog.decode('80','hex'); zero_count:=(56-(pg_catalog.octet_length(payload)%64)+64)%64; IF zero_count>0 THEN payload:=payload||pg_catalog.decode(repeat('00',zero_count),'hex'); END IF; length_hex:=pg_catalog.lpad(pg_catalog.to_hex(original_octets*8),16,'0'); payload:=payload||pg_catalog.decode(length_hex,'hex'); block_offset:=0; WHILE block_offset<pg_catalog.octet_length(payload) LOOP FOR word_ordinal IN 0..15 LOOP words[word_ordinal]:=( (pg_catalog.get_byte(payload,block_offset+word_ordinal*4)::bigint<<24) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+1)::bigint<<16) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+2)::bigint<<8) | pg_catalog.get_byte(payload,block_offset+word_ordinal*4+3)::bigint )&mask; END LOOP; FOR word_ordinal IN 16..79 LOOP temporary:=( words[word_ordinal-3] # words[word_ordinal-8] # words[word_ordinal-14] # words[word_ordinal-16] )&mask; words[word_ordinal]:=((temporary<<1)|(temporary>>31))&mask; END LOOP; a:=h0; b:=h1; c:=h2; d:=h3; e:=h4; FOR round_ordinal IN 0..79 LOOP IF round_ordinal<=19 THEN f:=((b&c)|((mask#b)&d))&mask; k:=1518500249; ELSIF round_ordinal<=39 THEN f:=(b#c#d)&mask; k:=1859775393; ELSIF round_ordinal<=59 THEN f:=((b&c)|(b&d)|(c&d))&mask; k:=2400959708; ELSE f:=(b#c#d)&mask; k:=3395469782; END IF; temporary:=( (((a<<5)|(a>>27))&mask)+f+e+k+words[round_ordinal] )&mask; e:=d; d:=c; c:=((b<<30)|(b>>2))&mask; b:=a; a:=temporary; END LOOP; h0:=(h0+a)&mask; h1:=(h1+b)&mask; h2:=(h2+c)&mask; h3:=(h3+d)&mask; h4:=(h4+e)&mask; block_offset:=block_offset+64; END LOOP; digest:=pg_catalog.decode( pg_catalog.lpad(pg_catalog.to_hex(h0),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h1),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h2),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h3),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h4),8,'0'), 'hex' ); digest:=pg_catalog.substring(digest,1,16); digest:=pg_"
        "catalog.set_byte( digest,6,(pg_catalog.get_byte(digest,6)&15)|80 ); digest:=pg_catalog.set_byte( digest,8,(pg_catalog.get_byte(digest,8)&63)|128 ); value_hex:=pg_catalog.encode(digest,'hex'); expected_attempt_id:=( pg_catalog.substring(value_hex,1,8)||'-'|| pg_catalog.substring(value_hex,9,4)||'-'|| pg_catalog.substring(value_hex,13,4)||'-'|| pg_catalog.substring(value_hex,17,4)||'-'|| pg_catalog.substring(value_hex,21,12) )::uuid; END; IF expected_attempt_id IS DISTINCT FROM attempt_id THEN RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPT_PREIMAGE_MISMATCH' USING ERRCODE='23514'; END IF; event_name:= '['||pg_catalog.to_json('task7-erasure-audit-event-v1'::text)::text||','|| pg_catalog.to_json(attempt_id::text)::text||','|| pg_catalog.to_json('task7.erasure.attempted'::text)::text||','|| pg_catalog.to_json('ATTEMPTED'::text)::text||']'; DECLARE payload bytea; original_octets bigint; zero_count integer; block_offset integer; word_ordinal integer; round_ordinal integer; words bigint[]:=pg_catalog.array_fill(0::bigint,ARRAY[80],ARRAY[0]); h0 bigint:=1732584193; h1 bigint:=4023233417; h2 bigint:=2562383102; h3 bigint:=271733878; h4 bigint:=3285377520; a bigint; b bigint; c bigint; d bigint; e bigint; f bigint; k bigint; temporary bigint; mask constant bigint:=4294967295; length_hex text; digest bytea; value_hex text; BEGIN payload:=pg_catalog.decode( replace('dd76cee1-28d5-55f8-b21a-eb64bc2e4be4'::uuid::text,'-',''),'hex' )||pg_catalog.convert_to(event_name,'UTF8'); original_octets:=pg_catalog.octet_length(payload); payload:=payload||pg_catalog.decode('80','hex'); zero_count:=(56-(pg_catalog.octet_length(payload)%64)+64)%64; IF zero_count>0 THEN payload:=payload||pg_catalog.decode(repeat('00',zero_count),'hex'); END IF; length_hex:=pg_catalog.lpad(pg_catalog.to_hex(original_octets*8),16,'0'); payload:=payload||pg_catalog.decode(length_hex,'hex'); block_offset:=0; WHILE block_offset<pg_catalog.octet_length(payload) LOOP FOR word_ordinal IN 0..15 LOOP words[word_ordinal]:=( (pg_cat"
        "alog.get_byte(payload,block_offset+word_ordinal*4)::bigint<<24) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+1)::bigint<<16) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+2)::bigint<<8) | pg_catalog.get_byte(payload,block_offset+word_ordinal*4+3)::bigint )&mask; END LOOP; FOR word_ordinal IN 16..79 LOOP temporary:=( words[word_ordinal-3] # words[word_ordinal-8] # words[word_ordinal-14] # words[word_ordinal-16] )&mask; words[word_ordinal]:=((temporary<<1)|(temporary>>31))&mask; END LOOP; a:=h0; b:=h1; c:=h2; d:=h3; e:=h4; FOR round_ordinal IN 0..79 LOOP IF round_ordinal<=19 THEN f:=((b&c)|((mask#b)&d))&mask; k:=1518500249; ELSIF round_ordinal<=39 THEN f:=(b#c#d)&mask; k:=1859775393; ELSIF round_ordinal<=59 THEN f:=((b&c)|(b&d)|(c&d))&mask; k:=2400959708; ELSE f:=(b#c#d)&mask; k:=3395469782; END IF; temporary:=( (((a<<5)|(a>>27))&mask)+f+e+k+words[round_ordinal] )&mask; e:=d; d:=c; c:=((b<<30)|(b>>2))&mask; b:=a; a:=temporary; END LOOP; h0:=(h0+a)&mask; h1:=(h1+b)&mask; h2:=(h2+c)&mask; h3:=(h3+d)&mask; h4:=(h4+e)&mask; block_offset:=block_offset+64; END LOOP; digest:=pg_catalog.decode( pg_catalog.lpad(pg_catalog.to_hex(h0),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h1),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h2),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h3),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h4),8,'0'), 'hex' ); digest:=pg_catalog.substring(digest,1,16); digest:=pg_catalog.set_byte( digest,6,(pg_catalog.get_byte(digest,6)&15)|80 ); digest:=pg_catalog.set_byte( digest,8,(pg_catalog.get_byte(digest,8)&63)|128 ); value_hex:=pg_catalog.encode(digest,'hex'); expected_event_id:=( pg_catalog.substring(value_hex,1,8)||'-'|| pg_catalog.substring(value_hex,9,4)||'-'|| pg_catalog.substring(value_hex,13,4)||'-'|| pg_catalog.substring(value_hex,17,4)||'-'|| pg_catalog.substring(value_hex,21,12) )::uuid; END; IF expected_event_id IS DISTINCT FROM (attempted_event->>'event_id')::uuid THEN RAISE EXCEPTION 'TASK7_ERASURE_EVENT_PREIMAGE_MISMATCH'"
        " USING ERRCODE='23514'; END IF; IF request_status='REQUESTED' THEN IF attempt_ordinal<>1 OR prior_status IS NOT NULL OR prior_event_id IS NOT NULL OR prior_event_hash IS NOT NULL THEN RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPT_LINEAGE_INVALID' USING ERRCODE='23514'; END IF; ELSE IF attempt_ordinal<=1 OR prior_status<>'FAILED' OR prior_event_id IS NULL OR prior_event_hash!~'^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPT_LINEAGE_INVALID' USING ERRCODE='23514'; END IF; SELECT count(*),COALESCE(pg_catalog.jsonb_agg(item),'[]'::jsonb) INTO prior_count,prior_matches FROM pg_catalog.jsonb_array_elements(lineage_events) AS events(item) WHERE item->>'event_id'=prior_event_id::text AND item->>'event_type'='task7.erasure.failed' AND item->'payload'->'safe'->>'outcome'='FAILED' AND (item->'payload'->'safe'->>'attempt_ordinal')::integer= attempt_ordinal-1 AND item->'payload'->'safe'->>'request_id'=$1::text; IF prior_count<>1 THEN RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_EVENT_INVALID' USING ERRCODE='23514'; END IF; prior_event:=prior_matches->0; prior_attempt_id:=(prior_event->'payload'->'safe'->>'attempt_id')::uuid; prior_attempt_name:= '['||pg_catalog.to_json('task7-erasure-attempt-v1'::text)::text||','|| pg_catalog.to_json($2::text)::text||','|| pg_catalog.to_json($3::text)::text||','|| pg_catalog.to_json($1::text)::text||','|| (prior_event->'payload'->'safe'->>'attempt_ordinal')||','|| COALESCE( pg_catalog.to_json( prior_event->'payload'->'safe'->>'prior_status' )::text, 'null' )||','|| COALESCE( pg_catalog.to_json( prior_event->'payload'->'safe'->>'prior_attempt_event_id' )::text, 'null' )||','|| COALESCE( pg_catalog.to_json( prior_event->'payload'->'safe'->>'prior_attempt_event_hash' )::text, 'null' )||']'; DECLARE payload bytea; original_octets bigint; zero_count integer; block_offset integer; word_ordinal integer; round_ordinal integer; words bigint[]:=pg_catalog.array_fill(0::bigint,ARRAY[80],ARRAY[0]); h0 bigint:=1732584193; h1 bigint:=4023233417; h2 bigint:=25623"
        "83102; h3 bigint:=271733878; h4 bigint:=3285377520; a bigint; b bigint; c bigint; d bigint; e bigint; f bigint; k bigint; temporary bigint; mask constant bigint:=4294967295; length_hex text; digest bytea; value_hex text; BEGIN payload:=pg_catalog.decode( replace('4520d8ed-e929-5486-821c-82f8901ae7fa'::uuid::text,'-',''),'hex' )||pg_catalog.convert_to(prior_attempt_name,'UTF8'); original_octets:=pg_catalog.octet_length(payload); payload:=payload||pg_catalog.decode('80','hex'); zero_count:=(56-(pg_catalog.octet_length(payload)%64)+64)%64; IF zero_count>0 THEN payload:=payload||pg_catalog.decode(repeat('00',zero_count),'hex'); END IF; length_hex:=pg_catalog.lpad(pg_catalog.to_hex(original_octets*8),16,'0'); payload:=payload||pg_catalog.decode(length_hex,'hex'); block_offset:=0; WHILE block_offset<pg_catalog.octet_length(payload) LOOP FOR word_ordinal IN 0..15 LOOP words[word_ordinal]:=( (pg_catalog.get_byte(payload,block_offset+word_ordinal*4)::bigint<<24) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+1)::bigint<<16) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+2)::bigint<<8) | pg_catalog.get_byte(payload,block_offset+word_ordinal*4+3)::bigint )&mask; END LOOP; FOR word_ordinal IN 16..79 LOOP temporary:=( words[word_ordinal-3] # words[word_ordinal-8] # words[word_ordinal-14] # words[word_ordinal-16] )&mask; words[word_ordinal]:=((temporary<<1)|(temporary>>31))&mask; END LOOP; a:=h0; b:=h1; c:=h2; d:=h3; e:=h4; FOR round_ordinal IN 0..79 LOOP IF round_ordinal<=19 THEN f:=((b&c)|((mask#b)&d))&mask; k:=1518500249; ELSIF round_ordinal<=39 THEN f:=(b#c#d)&mask; k:=1859775393; ELSIF round_ordinal<=59 THEN f:=((b&c)|(b&d)|(c&d))&mask; k:=2400959708; ELSE f:=(b#c#d)&mask; k:=3395469782; END IF; temporary:=( (((a<<5)|(a>>27))&mask)+f+e+k+words[round_ordinal] )&mask; e:=d; d:=c; c:=((b<<30)|(b>>2))&mask; b:=a; a:=temporary; END LOOP; h0:=(h0+a)&mask; h1:=(h1+b)&mask; h2:=(h2+c)&mask; h3:=(h3+d)&mask; h4:=(h4+e)&mask; block_offset:=block_offset+64; END "
        "LOOP; digest:=pg_catalog.decode( pg_catalog.lpad(pg_catalog.to_hex(h0),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h1),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h2),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h3),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h4),8,'0'), 'hex' ); digest:=pg_catalog.substring(digest,1,16); digest:=pg_catalog.set_byte( digest,6,(pg_catalog.get_byte(digest,6)&15)|80 ); digest:=pg_catalog.set_byte( digest,8,(pg_catalog.get_byte(digest,8)&63)|128 ); value_hex:=pg_catalog.encode(digest,'hex'); expected_prior_attempt_id:=( pg_catalog.substring(value_hex,1,8)||'-'|| pg_catalog.substring(value_hex,9,4)||'-'|| pg_catalog.substring(value_hex,13,4)||'-'|| pg_catalog.substring(value_hex,17,4)||'-'|| pg_catalog.substring(value_hex,21,12) )::uuid; END; IF expected_prior_attempt_id IS DISTINCT FROM prior_attempt_id THEN RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_ATTEMPT_PREIMAGE_MISMATCH' USING ERRCODE='23514'; END IF; prior_event_name:= '['|| pg_catalog.to_json('task7-erasure-audit-event-v1'::text)::text||','|| pg_catalog.to_json(prior_attempt_id::text)::text||','|| pg_catalog.to_json('task7.erasure.failed'::text)::text||','|| pg_catalog.to_json('FAILED'::text)::text||']'; DECLARE payload bytea; original_octets bigint; zero_count integer; block_offset integer; word_ordinal integer; round_ordinal integer; words bigint[]:=pg_catalog.array_fill(0::bigint,ARRAY[80],ARRAY[0]); h0 bigint:=1732584193; h1 bigint:=4023233417; h2 bigint:=2562383102; h3 bigint:=271733878; h4 bigint:=3285377520; a bigint; b bigint; c bigint; d bigint; e bigint; f bigint; k bigint; temporary bigint; mask constant bigint:=4294967295; length_hex text; digest bytea; value_hex text; BEGIN payload:=pg_catalog.decode( replace('dd76cee1-28d5-55f8-b21a-eb64bc2e4be4'::uuid::text,'-',''),'hex' )||pg_catalog.convert_to(prior_event_name,'UTF8'); original_octets:=pg_catalog.octet_length(payload); payload:=payload||pg_catalog.decode('80','hex'); zero_count:=(56-(pg_catalog.octet_length(payload)%64)+64"
        ")%64; IF zero_count>0 THEN payload:=payload||pg_catalog.decode(repeat('00',zero_count),'hex'); END IF; length_hex:=pg_catalog.lpad(pg_catalog.to_hex(original_octets*8),16,'0'); payload:=payload||pg_catalog.decode(length_hex,'hex'); block_offset:=0; WHILE block_offset<pg_catalog.octet_length(payload) LOOP FOR word_ordinal IN 0..15 LOOP words[word_ordinal]:=( (pg_catalog.get_byte(payload,block_offset+word_ordinal*4)::bigint<<24) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+1)::bigint<<16) | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+2)::bigint<<8) | pg_catalog.get_byte(payload,block_offset+word_ordinal*4+3)::bigint )&mask; END LOOP; FOR word_ordinal IN 16..79 LOOP temporary:=( words[word_ordinal-3] # words[word_ordinal-8] # words[word_ordinal-14] # words[word_ordinal-16] )&mask; words[word_ordinal]:=((temporary<<1)|(temporary>>31))&mask; END LOOP; a:=h0; b:=h1; c:=h2; d:=h3; e:=h4; FOR round_ordinal IN 0..79 LOOP IF round_ordinal<=19 THEN f:=((b&c)|((mask#b)&d))&mask; k:=1518500249; ELSIF round_ordinal<=39 THEN f:=(b#c#d)&mask; k:=1859775393; ELSIF round_ordinal<=59 THEN f:=((b&c)|(b&d)|(c&d))&mask; k:=2400959708; ELSE f:=(b#c#d)&mask; k:=3395469782; END IF; temporary:=( (((a<<5)|(a>>27))&mask)+f+e+k+words[round_ordinal] )&mask; e:=d; d:=c; c:=((b<<30)|(b>>2))&mask; b:=a; a:=temporary; END LOOP; h0:=(h0+a)&mask; h1:=(h1+b)&mask; h2:=(h2+c)&mask; h3:=(h3+d)&mask; h4:=(h4+e)&mask; block_offset:=block_offset+64; END LOOP; digest:=pg_catalog.decode( pg_catalog.lpad(pg_catalog.to_hex(h0),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h1),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h2),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h3),8,'0')|| pg_catalog.lpad(pg_catalog.to_hex(h4),8,'0'), 'hex' ); digest:=pg_catalog.substring(digest,1,16); digest:=pg_catalog.set_byte( digest,6,(pg_catalog.get_byte(digest,6)&15)|80 ); digest:=pg_catalog.set_byte( digest,8,(pg_catalog.get_byte(digest,8)&63)|128 ); value_hex:=pg_catalog.encode(digest,'hex'); expected_prior_"
        "event_id:=( pg_catalog.substring(value_hex,1,8)||'-'|| pg_catalog.substring(value_hex,9,4)||'-'|| pg_catalog.substring(value_hex,13,4)||'-'|| pg_catalog.substring(value_hex,17,4)||'-'|| pg_catalog.substring(value_hex,21,12) )::uuid; END; IF expected_prior_event_id IS DISTINCT FROM prior_event_id THEN RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_EVENT_PREIMAGE_MISMATCH' USING ERRCODE='23514'; END IF; IF (SELECT count(*) FROM pg_catalog.jsonb_object_keys(prior_event))<>10 OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys( prior_event->'actor'))<>5 OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys( prior_event->'request'))<>4 OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys( prior_event->'resource'))<>3 OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys( prior_event->'payload'))<>3 OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys( prior_event->'payload'->'safe'))<>7 THEN RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_EVENT_SHAPE_INVALID' USING ERRCODE='23514'; END IF; canonical_prior_event:= '{\"actor\":{'|| '\"actor_id\":'||pg_catalog.to_json(prior_event->'actor'->>'actor_id')::text||','|| '\"actor_type\":'||pg_catalog.to_json(prior_event->'actor'->>'actor_type')::text||','|| '\"ip\":'||pg_catalog.to_json(prior_event->'actor'->>'ip')::text||','|| '\"roles\":'||replace((prior_event->'actor'->'roles')::text,', ',',')||','|| '\"user_agent\":'||pg_catalog.to_json(prior_event->'actor'->>'user_agent')::text|| '},\"event_id\":'||pg_catalog.to_json(prior_event->>'event_id')::text|| ',\"event_type\":'||pg_catalog.to_json(prior_event->>'event_type')::text|| ',\"occurred_at\":'||pg_catalog.to_json(prior_event->>'occurred_at')::text|| ',\"payload\":{\"hashes\":'|| replace((prior_event->'payload'->'hashes')::text,', ',',')|| ',\"refs\":'||replace((prior_event->'payload'->'refs')::text,', ', ',')|| ',\"safe\":{\"attempt_id\":'||pg_catalog.to_json(prior_event->'payload'->'safe'->>'attempt_id')::text|| ',\"attempt_ordinal\":'||(prior_event->'payload'->'safe'->>'attempt_ordinal')|| ',\"outcome\":'||pg_catalog.to_"
        "json(prior_event->'payload'->'safe'->>'outcome')::text|| ',\"prior_attempt_event_hash\":'||COALESCE(pg_catalog.to_json(prior_event->'payload'->'safe'->>'prior_attempt_event_hash')::text,'null')|| ',\"prior_attempt_event_id\":'||COALESCE(pg_catalog.to_json(prior_event->'payload'->'safe'->>'prior_attempt_event_id')::text,'null')|| ',\"prior_status\":'||COALESCE(pg_catalog.to_json(prior_event->'payload'->'safe'->>'prior_status')::text,'null')|| ',\"request_id\":'||pg_catalog.to_json(prior_event->'payload'->'safe'->>'request_id')::text|| '}},\"request\":{\"method\":'||pg_catalog.to_json(prior_event->'request'->>'method')::text|| ',\"path\":'||pg_catalog.to_json(prior_event->'request'->>'path')::text|| ',\"request_id\":'||pg_catalog.to_json(prior_event->'request'->>'request_id')::text|| ',\"status_code\":'||(prior_event->'request'->>'status_code')|| '},\"resource\":{\"deal_id\":'||pg_catalog.to_json(prior_event->'resource'->>'deal_id')::text|| ',\"resource_id\":'||pg_catalog.to_json(prior_event->'resource'->>'resource_id')::text|| ',\"resource_type\":'||pg_catalog.to_json(prior_event->'resource'->>'resource_type')::text|| '},\"severity\":'||pg_catalog.to_json(prior_event->>'severity')::text|| ',\"summary\":'||pg_catalog.to_json(prior_event->>'summary')::text|| ',\"tenant_id\":'||pg_catalog.to_json(prior_event->>'tenant_id')::text||'}'; IF pg_catalog.encode( pg_catalog.sha256( pg_catalog.convert_to(canonical_prior_event,'UTF8') ), 'hex' ) IS DISTINCT FROM prior_event_hash THEN RAISE EXCEPTION 'TASK7_ERASURE_PRIOR_EVENT_HASH_MISMATCH' USING ERRCODE='23514'; END IF; END IF; IF EXISTS ( SELECT 1 FROM public.legal_holds h WHERE h.tenant_id=$2 AND h.lifted_at IS NULL AND ( (h.target_type='DEAL' AND h.target_id=$3::text) OR ( h.target_type='DOCUMENT' AND EXISTS ( SELECT 1 FROM public.document_artifacts a WHERE a.tenant_id=$2 AND a.deal_id=$3 AND h.target_id=a.doc_id::text ) ) OR ( h.target_type='ARTIFACT' AND EXISTS ( SELECT 1 FROM public.document_artifacts a WHERE a.tenant_id=$2 AND a.deal_id=$3 AND a.uri='i"
        "dis://'||h.target_id ) ) ) ) THEN RAISE EXCEPTION 'TASK7_ERASURE_BLOCKED_BY_HOLD' USING ERRCODE='23514'; END IF; SET CONSTRAINTS ALL DEFERRED; PERFORM pg_catalog.set_config('idis.erasure_context','true',true); DELETE FROM public.deliverable_manifest_references WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('deliverable_manifest_references',deleted_count); DELETE FROM public.run_consumer_receipts WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('run_consumer_receipts',deleted_count); DELETE FROM public.run_expected_consumer_edges WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('run_expected_consumer_edges',deleted_count); DELETE FROM public.methodology_result_defect_links WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_result_defect_links',deleted_count); DELETE FROM public.methodology_result_evidence_links WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_result_evidence_links',deleted_count); DELETE FROM public.methodology_result_sanad_links WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_result_sanad_links',deleted_count); DELETE FROM public.methodology_result_claim_links WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_result_claim_links',deleted_count); DELETE FROM public.run_applicability_decisions WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion"
        "_counts:=deletion_counts||pg_catalog.jsonb_build_object('run_applicability_decisions',deleted_count); DELETE FROM public.run_step_outputs WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('run_step_outputs',deleted_count); DELETE FROM public.methodology_result_factual_outputs WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_result_factual_outputs',deleted_count); DELETE FROM public.methodology_extraction_results WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_extraction_results',deleted_count); DELETE FROM public.calc_sanads WHERE calc_sanads.tenant_id=$2 AND calc_sanads.calc_id IN (SELECT deterministic_calculations.calc_id FROM public.deterministic_calculations WHERE deterministic_calculations.tenant_id=$2 AND deterministic_calculations.deal_id=$3); GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('calc_sanads',deleted_count); DELETE FROM public.deterministic_calculations WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('deterministic_calculations',deleted_count); DELETE FROM public.layer2_ic_findings WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('layer2_ic_findings',deleted_count); DELETE FROM public.layer2_ic_challenges WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('layer2_ic_challenges',deleted_count); DELETE FROM public.debate_sessions WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_cou"
        "nts||pg_catalog.jsonb_build_object('debate_sessions',deleted_count); DELETE FROM public.evidence_trust_findings WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('evidence_trust_findings',deleted_count); DELETE FROM public.validated_evidence_packages WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('validated_evidence_packages',deleted_count); DELETE FROM public.muhasabah_records WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('muhasabah_records',deleted_count); DELETE FROM public.deliverables WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('deliverables',deleted_count); DELETE FROM public.vector_embeddings WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('vector_embeddings',deleted_count); DELETE FROM public.run_input_bindings WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('run_input_bindings',deleted_count); DELETE FROM public.data_room_package_files WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('data_room_package_files',deleted_count); DELETE FROM public.break_glass_grants WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('break_glass_grants',deleted_count); DELETE FROM public.deal_assignments WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('deal_assignm"
        "ents',deleted_count); DELETE FROM public.defects WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('defects',deleted_count); DELETE FROM public.human_gate_actions WHERE human_gate_actions.tenant_id=$2 AND human_gate_actions.gate_id IN (SELECT human_gates.gate_id FROM public.human_gates WHERE human_gates.tenant_id=$2 AND human_gates.deal_id=$3); GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('human_gate_actions',deleted_count); DELETE FROM public.human_gates WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('human_gates',deleted_count); DELETE FROM public.methodology_extraction_attempts WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_extraction_attempts',deleted_count); DELETE FROM public.run_steps WHERE run_steps.tenant_id=$2 AND run_steps.run_id IN (SELECT runs.run_id FROM public.runs WHERE runs.tenant_id=$2 AND runs.deal_id=$3); GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('run_steps',deleted_count); DELETE FROM public.methodology_extraction_task_spans WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_extraction_task_spans',deleted_count); DELETE FROM public.methodology_extraction_tasks WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_extraction_tasks',deleted_count); DELETE FROM public.methodology_coverage_records WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('methodology_"
        "coverage_records',deleted_count); DELETE FROM public.run_output_objects WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('run_output_objects',deleted_count); DELETE FROM public.document_spans WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('document_spans',deleted_count); DELETE FROM public.document_parse_attempts WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('document_parse_attempts',deleted_count); DELETE FROM public.documents WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('documents',deleted_count); DELETE FROM public.document_artifacts WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('document_artifacts',deleted_count); DELETE FROM public.data_room_packages WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('data_room_packages',deleted_count); DELETE FROM public.source_lineage_objects WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('source_lineage_objects',deleted_count); DELETE FROM public.overrides WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('overrides',deleted_count); DELETE FROM public.run_methodology_selections WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('run_methodology_selections',deleted_count); DELETE FROM public.runs WHERE tenant_id=$"
        "2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('runs',deleted_count); DELETE FROM public.sanads WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('sanads',deleted_count); DELETE FROM public.evidence_items WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('evidence_items',deleted_count); DELETE FROM public.claims WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('claims',deleted_count); DELETE FROM public.deals WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('deals',deleted_count); DELETE FROM public.strict_preflight_proofs WHERE tenant_id=$2 AND deal_id=$3; GET DIAGNOSTICS deleted_count=ROW_COUNT; deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object('strict_preflight_proofs',deleted_count); deletion_counts:=deletion_counts||pg_catalog.jsonb_build_object( 'object_rows_reconciled',staged_object_rows ); SET CONSTRAINTS ALL IMMEDIATE; UPDATE public.erasure_requests SET status='EXECUTED', executed_by=attempted_actor_id, executed_at=pg_catalog.statement_timestamp(), counts=deletion_counts WHERE request_id=$1 AND tenant_id=$2 AND deal_id=$3 AND status=request_status RETURNING status,executed_by,executed_at,counts INTO persisted_status,persisted_actor,persisted_at,persisted_counts; IF NOT FOUND THEN RAISE EXCEPTION 'TASK7_ERASURE_REQUEST_FENCED' USING ERRCODE='40001'; END IF; RETURN pg_catalog.jsonb_build_object( 'status',persisted_status, 'executed_by',persisted_actor, 'executed_at',pg_catalog.to_char( persisted_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"' ), 'counts',persisted_counts ); EXCEPTION WHEN no_data_found OR too"
        "_many_rows OR invalid_text_representation OR numeric_value_out_of_range THEN RAISE EXCEPTION 'TASK7_ERASURE_ATTEMPTED_EVENT_INVALID' USING ERRCODE='23514'; END$$"
    ),
)

_CURRENT_ERASURE_ACL_SQL_V1: Final = (
    "ALTER FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) OWNER TO idis_erasure_function_owner",
    "REVOKE ALL ON FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) FROM PUBLIC",
    "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.deliverable_manifest_references FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deliverable_manifest_references FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_consumer_receipts FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_consumer_receipts FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_expected_consumer_edges FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_expected_consumer_edges FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_defect_links FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_defect_links FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_evidence_links FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_evidence_links FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_sanad_links FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_sanad_links FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_claim_links FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_claim_links FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_applicability_decisions FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_applicability_decisions FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_step_outputs FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_step_outputs FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_factual_outputs FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_result_factual_outputs FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_results FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_results FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.calc_sanads FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.calc_sanads FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.deterministic_calculations FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deterministic_calculations FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.layer2_ic_findings FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.layer2_ic_findings FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.layer2_ic_challenges FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.layer2_ic_challenges FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.debate_sessions FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.debate_sessions FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.evidence_trust_findings FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.evidence_trust_findings FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.validated_evidence_packages FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.validated_evidence_packages FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.muhasabah_records FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.muhasabah_records FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.deliverables FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deliverables FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.vector_embeddings FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.vector_embeddings FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_input_bindings FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_input_bindings FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.data_room_package_files FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.data_room_package_files FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.break_glass_grants FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.break_glass_grants FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.deal_assignments FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deal_assignments FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.defects FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.defects FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.human_gate_actions FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.human_gate_actions FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.human_gates FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.human_gates FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_attempts FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_attempts FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_steps FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_steps FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_task_spans FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_task_spans FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_tasks FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_extraction_tasks FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_coverage_records FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.methodology_coverage_records FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_output_objects FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_output_objects FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_spans FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_spans FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_parse_attempts FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_parse_attempts FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.documents FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.documents FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_artifacts FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.document_artifacts FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.data_room_packages FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.data_room_packages FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.source_lineage_objects FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.source_lineage_objects FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.overrides FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.overrides FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_methodology_selections FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.run_methodology_selections FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.runs FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.runs FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.sanads FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.sanads FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.evidence_items FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.evidence_items FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.claims FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.claims FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.deals FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.deals FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.strict_preflight_proofs FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.strict_preflight_proofs FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.erasure_requests FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.erasure_requests FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.legal_holds FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.legal_holds FROM idis_erasure_executor",
    "REVOKE ALL PRIVILEGES ON TABLE public.audit_events FROM idis_erasure_function_owner",
    "REVOKE ALL PRIVILEGES ON TABLE public.audit_events FROM idis_erasure_executor",
    "GRANT USAGE ON SCHEMA public TO idis_erasure_function_owner",
    "GRANT USAGE ON SCHEMA public TO idis_erasure_executor",
    "GRANT SELECT (request_id,tenant_id,deal_id,status,executed_by,executed_at,counts) ON TABLE public.erasure_requests TO idis_erasure_function_owner",
    "GRANT SELECT (event_id,tenant_id,event_type,event) ON TABLE public.audit_events TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id,calc_id) ON TABLE public.deterministic_calculations TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id,run_id) ON TABLE public.runs TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id,gate_id) ON TABLE public.human_gates TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,target_type,target_id,lifted_at) ON TABLE public.legal_holds TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id,doc_id,uri) ON TABLE public.document_artifacts TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.deliverable_manifest_references TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.run_consumer_receipts TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.run_expected_consumer_edges TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_result_defect_links TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_result_evidence_links TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_result_sanad_links TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_result_claim_links TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.run_applicability_decisions TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.run_step_outputs TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_result_factual_outputs TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_extraction_results TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,calc_id) ON TABLE public.calc_sanads TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.layer2_ic_findings TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.layer2_ic_challenges TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.debate_sessions TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.evidence_trust_findings TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.validated_evidence_packages TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.muhasabah_records TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.deliverables TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.vector_embeddings TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.run_input_bindings TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.data_room_package_files TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.break_glass_grants TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.deal_assignments TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.defects TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,gate_id) ON TABLE public.human_gate_actions TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_extraction_attempts TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,run_id) ON TABLE public.run_steps TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_extraction_task_spans TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_extraction_tasks TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.methodology_coverage_records TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.run_output_objects TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.document_spans TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.document_parse_attempts TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.documents TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.data_room_packages TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.source_lineage_objects TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.overrides TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.run_methodology_selections TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.sanads TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.evidence_items TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.claims TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.deals TO idis_erasure_function_owner",
    "GRANT SELECT (tenant_id,deal_id) ON TABLE public.strict_preflight_proofs TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.deliverable_manifest_references TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.run_consumer_receipts TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.run_expected_consumer_edges TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_result_defect_links TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_result_evidence_links TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_result_sanad_links TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_result_claim_links TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.run_applicability_decisions TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.run_step_outputs TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_result_factual_outputs TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_extraction_results TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.calc_sanads TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.deterministic_calculations TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.layer2_ic_findings TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.layer2_ic_challenges TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.debate_sessions TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.evidence_trust_findings TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.validated_evidence_packages TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.muhasabah_records TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.deliverables TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.vector_embeddings TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.run_input_bindings TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.data_room_package_files TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.break_glass_grants TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.deal_assignments TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.defects TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.human_gate_actions TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.human_gates TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_extraction_attempts TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.run_steps TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_extraction_task_spans TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_extraction_tasks TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.methodology_coverage_records TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.run_output_objects TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.document_spans TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.document_parse_attempts TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.documents TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.document_artifacts TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.data_room_packages TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.source_lineage_objects TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.overrides TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.run_methodology_selections TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.runs TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.sanads TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.evidence_items TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.claims TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.deals TO idis_erasure_function_owner",
    "GRANT DELETE ON TABLE public.strict_preflight_proofs TO idis_erasure_function_owner",
    "GRANT UPDATE (request_id,status,executed_by,executed_at,counts) ON TABLE public.erasure_requests TO idis_erasure_function_owner",
    "GRANT EXECUTE ON FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) TO idis_erasure_executor",
)

_LEGACY_GUARD_UPGRADE_SQL_V1: Final = (
    "CREATE FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) RETURNS boolean "
    "LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$SELECT "
    "session_user='idis_erasure_executor' AND current_user='idis_erasure_function_owner' AND "
    "pg_catalog.current_setting('idis.tenant_id',true)=$1::text AND "
    "pg_catalog.current_setting('idis.erasure_context',true)='true' AND EXISTS (SELECT 1 FROM "
    "public.erasure_requests e WHERE e.tenant_id=$1 AND e.deal_id=$2 AND e.status IN "
    "('REQUESTED','FAILED'))$$",
    "ALTER FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) OWNER TO "
    "idis_erasure_function_owner",
    "REVOKE ALL ON FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid) FROM PUBLIC",
    "CREATE OR REPLACE FUNCTION public.idis_prevent_strict_preflight_proof_mutation() RETURNS trigger "
    "LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN IF TG_OP='INSERT' THEN IF "
    "NEW.consumed_at IS NULL AND NEW.consumed_by_run_id IS NULL THEN PERFORM 1 FROM public.deals "
    "WHERE deals.tenant_id=NEW.tenant_id AND deals.deal_id=NEW.deal_id FOR KEY SHARE; IF FOUND THEN "
    "RETURN NEW; END IF; END IF; RAISE EXCEPTION 'Proof insert requires unconsumed state and a live "
    "same-tenant deal'; ELSIF TG_OP='UPDATE' THEN IF NEW.proof_id IS NOT DISTINCT FROM OLD.proof_id "
    "AND NEW.tenant_id IS NOT DISTINCT FROM OLD.tenant_id AND NEW.deal_id IS NOT DISTINCT FROM "
    "OLD.deal_id AND NEW.corpus_hash IS NOT DISTINCT FROM OLD.corpus_hash AND "
    "NEW.strict_profile_generation IS NOT DISTINCT FROM OLD.strict_profile_generation AND "
    "NEW.strict_profile_hmac IS NOT DISTINCT FROM OLD.strict_profile_hmac AND "
    "NEW.pipeline_contract_version IS NOT DISTINCT FROM OLD.pipeline_contract_version AND "
    "NEW.pipeline_contract_hash IS NOT DISTINCT FROM OLD.pipeline_contract_hash AND "
    "NEW.expected_consumer_edge_rule_hash IS NOT DISTINCT FROM OLD.expected_consumer_edge_rule_hash "
    "AND NEW.static_decision_set_hash IS NOT DISTINCT FROM OLD.static_decision_set_hash AND "
    "NEW.methodology_version_hashes IS NOT DISTINCT FROM OLD.methodology_version_hashes AND "
    "NEW.promoted_pointer_hash IS NOT DISTINCT FROM OLD.promoted_pointer_hash AND "
    "NEW.provider_receipt_set_hash IS NOT DISTINCT FROM OLD.provider_receipt_set_hash AND "
    "NEW.dependency_receipt_set_hash IS NOT DISTINCT FROM OLD.dependency_receipt_set_hash AND "
    "NEW.binding_hash IS NOT DISTINCT FROM OLD.binding_hash AND NEW.created_at IS NOT DISTINCT FROM "
    "OLD.created_at AND NEW.expires_at IS NOT DISTINCT FROM OLD.expires_at AND OLD.consumed_at IS "
    "NULL AND OLD.consumed_by_run_id IS NULL AND OLD.expires_at>pg_catalog.statement_timestamp() AND "
    "NEW.consumed_at IS NOT NULL AND NEW.consumed_at<=OLD.expires_at AND NEW.consumed_by_run_id IS "
    "NOT NULL AND EXISTS (SELECT 1 FROM public.runs WHERE runs.run_id=NEW.consumed_by_run_id AND "
    "runs.tenant_id=NEW.tenant_id AND runs.deal_id=NEW.deal_id AND "
    "runs.strict_preflight_proof_id=NEW.proof_id) THEN RETURN NEW; END IF; RAISE EXCEPTION 'Strict "
    "proof immutable; only first reciprocal consumption is allowed'; ELSIF TG_OP='DELETE' THEN IF "
    "session_user='idis_erasure_executor' AND current_user='idis_erasure_function_owner' THEN IF "
    "public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END "
    "IF; END IF; IF NOT EXISTS (SELECT 1 FROM public.deals WHERE deals.tenant_id=OLD.tenant_id AND "
    "deals.deal_id=OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION 'Strict preflight proof "
    "cannot be deleted while its same-tenant deal exists'; END IF; RAISE EXCEPTION 'Unsupported "
    "strict preflight proof mutation'; END$$",
    "CREATE OR REPLACE FUNCTION public.idis_task5_selection_guard() RETURNS trigger LANGUAGE plpgsql "
    "SET search_path=pg_catalog,pg_temp AS $$DECLARE commercial_count integer; financial_count "
    "integer; BEGIN IF TG_OP='DELETE' THEN IF session_user='idis_erasure_executor' AND "
    "current_user='idis_erasure_function_owner' THEN IF "
    "public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END "
    "IF; END IF; END IF; IF TG_OP<>'INSERT' THEN RAISE EXCEPTION "
    "'RUN_METHODOLOGY_SELECTION_IMMUTABLE' USING ERRCODE='23514'; END IF; SELECT question_count INTO "
    "commercial_count FROM public.methodology_versions WHERE tenant_id=NEW.tenant_id AND "
    "methodology_version_record_id=NEW.commercial_dd_version_record_id AND "
    "methodology_version_id=NEW.commercial_methodology_version_id AND "
    "version_hash=NEW.commercial_version_hash AND methodology_type='commercial_dd' AND "
    "status='FROZEN'; SELECT question_count INTO financial_count FROM public.methodology_versions "
    "WHERE tenant_id=NEW.tenant_id AND "
    "methodology_version_record_id=NEW.financial_dd_version_record_id AND "
    "methodology_version_id=NEW.financial_methodology_version_id AND "
    "version_hash=NEW.financial_version_hash AND methodology_type='financial_dd' AND status='FROZEN'; "
    "IF commercial_count IS DISTINCT FROM 21 OR financial_count IS DISTINCT FROM 79 OR "
    "commercial_count+financial_count<>NEW.selected_question_count THEN RAISE EXCEPTION "
    "'RUN_METHODOLOGY_SELECTION_INVALID' USING ERRCODE='23514'; END IF; RETURN NEW; END$$",
    "CREATE OR REPLACE FUNCTION public.idis_task5_coverage_scope_guard() RETURNS trigger LANGUAGE "
    "plpgsql SET search_path=pg_catalog,pg_temp AS $$BEGIN IF TG_OP='DELETE' THEN IF "
    "session_user='idis_erasure_executor' AND current_user='idis_erasure_function_owner' THEN IF "
    "public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END "
    "IF; END IF; RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING ERRCODE='23514'; END "
    "IF; IF TG_OP='UPDATE' AND (NEW.coverage_record_id IS DISTINCT FROM OLD.coverage_record_id OR "
    "NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.deal_id IS DISTINCT FROM OLD.deal_id OR "
    "NEW.run_id IS DISTINCT FROM OLD.run_id OR NEW.selection_id IS DISTINCT FROM OLD.selection_id OR "
    "NEW.methodology_version_record_id IS DISTINCT FROM OLD.methodology_version_record_id OR "
    "NEW.methodology_version_id IS DISTINCT FROM OLD.methodology_version_id OR "
    "NEW.methodology_question_record_id IS DISTINCT FROM OLD.methodology_question_record_id OR "
    "NEW.methodology_question_id IS DISTINCT FROM OLD.methodology_question_id OR NEW.methodology_type "
    "IS DISTINCT FROM OLD.methodology_type OR NEW.created_at IS DISTINCT FROM OLD.created_at) THEN "
    "RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING ERRCODE='23514'; END IF; IF NOT "
    "EXISTS (SELECT 1 FROM public.run_methodology_selections s JOIN public.methodology_questions q ON "
    "q.tenant_id=NEW.tenant_id AND q.methodology_version_record_id=NEW.methodology_version_record_id "
    "AND q.methodology_version_id=NEW.methodology_version_id AND "
    "q.methodology_question_record_id=NEW.methodology_question_record_id AND "
    "q.methodology_question_id=NEW.methodology_question_id AND "
    "q.methodology_type=NEW.methodology_type WHERE s.tenant_id=NEW.tenant_id AND "
    "s.deal_id=NEW.deal_id AND s.run_id=NEW.run_id AND s.selection_id=NEW.selection_id AND "
    "((q.methodology_type='commercial_dd' AND "
    "q.methodology_version_record_id=s.commercial_dd_version_record_id) OR "
    "(q.methodology_type='financial_dd' AND "
    "q.methodology_version_record_id=s.financial_dd_version_record_id))) THEN RAISE EXCEPTION "
    "'METHODOLOGY_COVERAGE_SCOPE_INVALID' USING ERRCODE='23514'; END IF; "
    "NEW.updated_at:=pg_catalog.now(); RETURN NEW; END$$",
    "CREATE OR REPLACE FUNCTION public.idis_task6_erasure_requested(checked_tenant uuid,checked_deal "
    "uuid) RETURNS boolean LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $$BEGIN IF "
    "NOT (session_user='idis_erasure_executor' AND current_user='idis_erasure_function_owner') THEN "
    "RETURN false; END IF; RETURN "
    "public.idis_task7_approved_erasure_context_v1(checked_tenant,checked_deal); END$$",
    "CREATE OR REPLACE FUNCTION public.idis_task6_current_lease_is_valid(checked_tenant uuid,checked_deal uuid,checked_run uuid) RETURNS boolean LANGUAGE plpgsql STABLE SET search_path=pg_catalog,pg_temp AS $$DECLARE current_tenant_id uuid; current_deal_id uuid; current_run_id uuid; current_owner_id uuid; current_fencing_token bigint; BEGIN BEGIN current_tenant_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_tenant_id',true),'')::uuid; current_deal_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_deal_id',true),'')::uuid; current_run_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_run_id',true),'')::uuid; current_owner_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_owner_id',true),'')::uuid; current_fencing_token:=NULLIF(pg_catalog.current_setting('idis.worker_lease_fencing_token',true),'')::bigint; EXCEPTION WHEN OTHERS THEN RETURN false; END; RETURN EXISTS (SELECT 1 FROM public.runs r WHERE r.tenant_id=checked_tenant AND r.deal_id=checked_deal AND r.run_id=checked_run AND r.status='RUNNING' AND r.tenant_id=current_tenant_id AND r.deal_id=current_deal_id AND r.run_id=current_run_id AND r.lease_owner_id=current_owner_id AND r.lease_fencing_token=current_fencing_token AND r.lease_expires_at>pg_catalog.statement_timestamp()); END$$",
    "CREATE OR REPLACE FUNCTION public.idis_task6_immutable_guard() RETURNS trigger LANGUAGE plpgsql "
    "SET search_path=pg_catalog,pg_temp AS $$BEGIN IF TG_OP='INSERT' THEN IF NOT "
    "public.idis_task6_current_lease_is_valid(NEW.tenant_id,NEW.deal_id,NEW.run_id) THEN RAISE "
    "EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; RETURN NEW; END IF; IF "
    "TG_OP='DELETE' THEN IF session_user='idis_erasure_executor' AND "
    "current_user='idis_erasure_function_owner' THEN IF "
    "public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END "
    "IF; END IF; END IF; RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE' USING "
    "ERRCODE='23514'; END$$",
    "CREATE OR REPLACE FUNCTION public.idis_task6_attempt_guard() RETURNS trigger LANGUAGE plpgsql "
    "SET search_path=pg_catalog,pg_temp AS $$DECLARE current_tenant_id uuid; current_deal_id uuid; "
    "current_run_id uuid; current_owner_id uuid; current_fencing_token bigint; BEGIN IF "
    "TG_OP='DELETE' THEN IF session_user='idis_erasure_executor' AND "
    "current_user='idis_erasure_function_owner' THEN IF "
    "public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN OLD; END "
    "IF; END IF; RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE' USING ERRCODE='23514'; "
    "END IF; BEGIN "
    "current_tenant_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_tenant_id',true),'')::uuid; "
    "current_deal_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_deal_id',true),'')::uuid; "
    "current_run_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_run_id',true),'')::uuid; "
    "current_owner_id:=NULLIF(pg_catalog.current_setting('idis.worker_lease_owner_id',true),'')::uuid; "
    "current_fencing_token:=NULLIF(pg_catalog.current_setting('idis.worker_lease_fencing_token',true),'')::bigint; "
    "EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END; IF "
    "NOT EXISTS (SELECT 1 FROM public.runs r WHERE r.tenant_id=NEW.tenant_id AND "
    "r.deal_id=NEW.deal_id AND r.run_id=NEW.run_id AND r.status='RUNNING' AND "
    "r.tenant_id=current_tenant_id AND r.deal_id=current_deal_id AND r.run_id=current_run_id AND "
    "r.lease_owner_id=current_owner_id AND r.lease_fencing_token=current_fencing_token AND "
    "r.lease_expires_at>pg_catalog.statement_timestamp()) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' "
    "USING ERRCODE='40001'; END IF; IF NOT EXISTS (SELECT 1 FROM public.run_steps s WHERE "
    "s.tenant_id=NEW.tenant_id AND s.run_id=NEW.run_id AND s.step_id=NEW.run_step_id AND "
    "s.step_name='METHODOLOGY_EXTRACTION_TASK_EXECUTION' AND s.step_order=6 AND s.status='RUNNING') "
    "THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001'; END IF; IF TG_OP='INSERT' THEN "
    "IF NEW.status<>'STARTED' THEN RAISE EXCEPTION 'METHODOLOGY_ATTEMPT_MUST_START' USING "
    "ERRCODE='23514'; END IF; IF NEW.lease_owner_id<>current_owner_id OR "
    "NEW.lease_fencing_token<>current_fencing_token THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING "
    "ERRCODE='40001'; END IF; RETURN NEW; END IF; IF OLD.status<>'STARTED' OR NEW.status NOT IN "
    "('SUCCEEDED','FAILED','OUTCOME_UNKNOWN') OR "
    "(pg_catalog.to_jsonb(NEW)-ARRAY['status','observed_model','provider_request_id','provider_response_sha256','dispatched_call_sha256','completed_at','latency_ms','input_tokens','output_tokens','stop_reason','budget_terminal_state','receipt_sha256','safe_failure_code']::text[]) "
    "IS DISTINCT FROM "
    "(pg_catalog.to_jsonb(OLD)-ARRAY['status','observed_model','provider_request_id','provider_response_sha256','dispatched_call_sha256','completed_at','latency_ms','input_tokens','output_tokens','stop_reason','budget_terminal_state','receipt_sha256','safe_failure_code']::text[]) "
    "THEN RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE' USING ERRCODE='23514'; END IF; "
    "IF NEW.status IN ('SUCCEEDED','FAILED') AND (current_owner_id<>OLD.lease_owner_id OR "
    "current_fencing_token<>OLD.lease_fencing_token) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING "
    "ERRCODE='40001'; END IF; IF NEW.status='OUTCOME_UNKNOWN' AND NOT "
    "((current_owner_id=OLD.lease_owner_id AND current_fencing_token=OLD.lease_fencing_token) OR "
    "current_fencing_token>OLD.lease_fencing_token) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING "
    "ERRCODE='40001'; END IF; RETURN NEW; END$$",
    "CREATE OR REPLACE FUNCTION public.idis_task6_validate_result_outputs() RETURNS trigger LANGUAGE "
    "plpgsql SET search_path=pg_catalog,pg_temp AS $$DECLARE checked_result uuid; checked_outcome "
    "text; output_count bigint; BEGIN IF TG_OP='DELETE' THEN IF session_user='idis_erasure_executor' "
    "AND current_user='idis_erasure_function_owner' THEN IF "
    "public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) THEN RETURN NULL; END "
    "IF; END IF; END IF; checked_result:=COALESCE(NEW.result_id,OLD.result_id); SELECT outcome INTO "
    "checked_outcome FROM public.methodology_extraction_results WHERE result_id=checked_result; IF "
    "checked_outcome IS NULL THEN RETURN NULL; END IF; SELECT pg_catalog.count(*) INTO output_count "
    "FROM public.methodology_result_factual_outputs WHERE result_id=checked_result; IF "
    "(checked_outcome='EVIDENCE_FOUND' AND output_count=0) OR (checked_outcome='EVIDENCE_NOT_FOUND' "
    "AND output_count<>0) THEN RAISE EXCEPTION 'METHODOLOGY_RESULT_FACTUAL_OUTPUT_MISMATCH' USING "
    "ERRCODE='23514'; END IF; RETURN NULL; END$$",
)

_LEGACY_GUARD_DOWNGRADE_SQL_V1: Final = (
    "CREATE OR REPLACE FUNCTION idis_prevent_strict_preflight_proof_mutation() RETURNS TRIGGER AS $$ "
    "BEGIN IF TG_OP = 'INSERT' THEN IF NEW.consumed_at IS NULL AND NEW.consumed_by_run_id IS NULL "
    "THEN PERFORM 1 FROM deals WHERE deals.tenant_id = NEW.tenant_id AND deals.deal_id = NEW.deal_id "
    "FOR KEY SHARE; IF FOUND THEN RETURN NEW; END IF; END IF; RAISE EXCEPTION 'Proof insert requires "
    "unconsumed state and a live same-tenant deal'; ELSIF TG_OP = 'UPDATE' THEN IF NEW.proof_id IS "
    "NOT DISTINCT FROM OLD.proof_id AND NEW.tenant_id IS NOT DISTINCT FROM OLD.tenant_id AND "
    "NEW.deal_id IS NOT DISTINCT FROM OLD.deal_id AND NEW.corpus_hash IS NOT DISTINCT FROM "
    "OLD.corpus_hash AND NEW.strict_profile_generation IS NOT DISTINCT FROM "
    "OLD.strict_profile_generation AND NEW.strict_profile_hmac IS NOT DISTINCT FROM "
    "OLD.strict_profile_hmac AND NEW.pipeline_contract_version IS NOT DISTINCT FROM "
    "OLD.pipeline_contract_version AND NEW.pipeline_contract_hash IS NOT DISTINCT FROM "
    "OLD.pipeline_contract_hash AND NEW.expected_consumer_edge_rule_hash IS NOT DISTINCT FROM "
    "OLD.expected_consumer_edge_rule_hash AND NEW.static_decision_set_hash IS NOT DISTINCT FROM "
    "OLD.static_decision_set_hash AND NEW.methodology_version_hashes IS NOT DISTINCT FROM "
    "OLD.methodology_version_hashes AND NEW.promoted_pointer_hash IS NOT DISTINCT FROM "
    "OLD.promoted_pointer_hash AND NEW.provider_receipt_set_hash IS NOT DISTINCT FROM "
    "OLD.provider_receipt_set_hash AND NEW.dependency_receipt_set_hash IS NOT DISTINCT FROM "
    "OLD.dependency_receipt_set_hash AND NEW.binding_hash IS NOT DISTINCT FROM OLD.binding_hash AND "
    "NEW.created_at IS NOT DISTINCT FROM OLD.created_at AND NEW.expires_at IS NOT DISTINCT FROM "
    "OLD.expires_at AND OLD.consumed_at IS NULL AND OLD.consumed_by_run_id IS NULL AND OLD.expires_at "
    "> statement_timestamp() AND NEW.consumed_at IS NOT NULL AND NEW.consumed_at <= OLD.expires_at "
    "AND NEW.consumed_by_run_id IS NOT NULL AND EXISTS ( SELECT 1 FROM runs WHERE runs.run_id = "
    "NEW.consumed_by_run_id AND runs.tenant_id = NEW.tenant_id AND runs.deal_id = NEW.deal_id AND "
    "runs.strict_preflight_proof_id = NEW.proof_id ) THEN RETURN NEW; END IF; RAISE EXCEPTION 'Strict "
    "proof immutable; only first reciprocal consumption is allowed'; ELSIF TG_OP = 'DELETE' THEN IF "
    "NOT EXISTS ( SELECT 1 FROM deals WHERE deals.tenant_id = OLD.tenant_id AND deals.deal_id = "
    "OLD.deal_id ) THEN RETURN OLD; END IF; RAISE EXCEPTION 'Strict preflight proof cannot be deleted "
    "while its same-tenant deal exists'; END IF; RAISE EXCEPTION 'Unsupported strict preflight proof "
    "mutation'; END; $$ LANGUAGE plpgsql;",
    "CREATE OR REPLACE FUNCTION idis_task5_selection_guard() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "DECLARE commercial_count integer; financial_count integer; BEGIN IF TG_OP = 'DELETE' AND EXISTS "
    "( SELECT 1 FROM erasure_requests e WHERE e.tenant_id = OLD.tenant_id AND e.deal_id = OLD.deal_id "
    "AND e.status = 'REQUESTED' ) THEN RETURN OLD; END IF; IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION "
    "'RUN_METHODOLOGY_SELECTION_IMMUTABLE' USING ERRCODE = '23514'; END IF; SELECT question_count "
    "INTO commercial_count FROM methodology_versions WHERE tenant_id = NEW.tenant_id AND "
    "methodology_version_record_id = NEW.commercial_dd_version_record_id AND methodology_version_id = "
    "NEW.commercial_methodology_version_id AND version_hash = NEW.commercial_version_hash AND "
    "methodology_type = 'commercial_dd' AND status = 'FROZEN'; SELECT question_count INTO "
    "financial_count FROM methodology_versions WHERE tenant_id = NEW.tenant_id AND "
    "methodology_version_record_id = NEW.financial_dd_version_record_id AND methodology_version_id = "
    "NEW.financial_methodology_version_id AND version_hash = NEW.financial_version_hash AND "
    "methodology_type = 'financial_dd' AND status = 'FROZEN'; IF commercial_count IS DISTINCT FROM 21 "
    "OR financial_count IS DISTINCT FROM 79 OR commercial_count + financial_count <> "
    "NEW.selected_question_count THEN RAISE EXCEPTION 'RUN_METHODOLOGY_SELECTION_INVALID' USING "
    "ERRCODE = '23514'; END IF; RETURN NEW; END $$",
    "CREATE OR REPLACE FUNCTION idis_task5_coverage_scope_guard() RETURNS trigger LANGUAGE plpgsql AS "
    "$$ BEGIN IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; IF TG_OP = 'UPDATE' AND ( "
    "NEW.coverage_record_id IS DISTINCT FROM OLD.coverage_record_id OR NEW.tenant_id IS DISTINCT FROM "
    "OLD.tenant_id OR NEW.deal_id IS DISTINCT FROM OLD.deal_id OR NEW.run_id IS DISTINCT FROM "
    "OLD.run_id OR NEW.selection_id IS DISTINCT FROM OLD.selection_id OR "
    "NEW.methodology_version_record_id IS DISTINCT FROM OLD.methodology_version_record_id OR "
    "NEW.methodology_version_id IS DISTINCT FROM OLD.methodology_version_id OR "
    "NEW.methodology_question_record_id IS DISTINCT FROM OLD.methodology_question_record_id OR "
    "NEW.methodology_question_id IS DISTINCT FROM OLD.methodology_question_id OR NEW.methodology_type "
    "IS DISTINCT FROM OLD.methodology_type OR NEW.created_at IS DISTINCT FROM OLD.created_at ) THEN "
    "RAISE EXCEPTION 'METHODOLOGY_COVERAGE_SCOPE_IMMUTABLE' USING ERRCODE = '23514'; END IF; IF NOT "
    "EXISTS ( SELECT 1 FROM run_methodology_selections s JOIN methodology_questions q ON q.tenant_id "
    "= NEW.tenant_id AND q.methodology_version_record_id = NEW.methodology_version_record_id AND "
    "q.methodology_version_id = NEW.methodology_version_id AND q.methodology_question_record_id = "
    "NEW.methodology_question_record_id AND q.methodology_question_id = NEW.methodology_question_id "
    "AND q.methodology_type = NEW.methodology_type WHERE s.tenant_id = NEW.tenant_id AND s.deal_id = "
    "NEW.deal_id AND s.run_id = NEW.run_id AND s.selection_id = NEW.selection_id AND ( "
    "(q.methodology_type = 'commercial_dd' AND q.methodology_version_record_id = "
    "s.commercial_dd_version_record_id) OR (q.methodology_type = 'financial_dd' AND "
    "q.methodology_version_record_id = s.financial_dd_version_record_id) ) ) THEN RAISE EXCEPTION "
    "'METHODOLOGY_COVERAGE_SCOPE_INVALID' USING ERRCODE = '23514'; END IF; NEW.updated_at := now(); "
    "RETURN NEW; END $$",
    "CREATE OR REPLACE FUNCTION idis_task6_erasure_requested( checked_tenant UUID, checked_deal UUID "
    ") RETURNS boolean LANGUAGE sql STABLE AS $$ SELECT EXISTS ( SELECT 1 FROM erasure_requests e "
    "WHERE e.tenant_id = checked_tenant AND e.deal_id = checked_deal AND e.status = 'REQUESTED' ) $$",
    "\n        CREATE OR REPLACE FUNCTION idis_task6_current_lease_is_valid(\n            checked_tenant UUID, checked_deal UUID, checked_run UUID\n        ) RETURNS boolean LANGUAGE plpgsql STABLE AS $$\n        DECLARE\n            current_tenant_id UUID;\n            current_deal_id UUID;\n            current_run_id UUID;\n            current_owner_id UUID;\n            current_fencing_token BIGINT;\n        BEGIN\n            BEGIN\n                current_tenant_id := NULLIF(\n                    current_setting('idis.worker_lease_tenant_id', true), ''\n                )::uuid;\n                current_deal_id := NULLIF(\n                    current_setting('idis.worker_lease_deal_id', true), ''\n                )::uuid;\n                current_run_id := NULLIF(\n                    current_setting('idis.worker_lease_run_id', true), ''\n                )::uuid;\n                current_owner_id := NULLIF(\n                    current_setting('idis.worker_lease_owner_id', true), ''\n                )::uuid;\n                current_fencing_token := NULLIF(\n                    current_setting('idis.worker_lease_fencing_token', true), ''\n                )::bigint;\n            EXCEPTION WHEN OTHERS THEN\n                RETURN false;\n            END;\n            RETURN EXISTS (\n                SELECT 1 FROM runs r\n                WHERE r.tenant_id = checked_tenant\n                  AND r.deal_id = checked_deal\n                  AND r.run_id = checked_run\n                  AND r.status = 'RUNNING'\n                  AND r.tenant_id = current_tenant_id\n                  AND r.deal_id = current_deal_id\n                  AND r.run_id = current_run_id\n                  AND r.lease_owner_id = current_owner_id\n                  AND r.lease_fencing_token = current_fencing_token\n                  AND r.lease_expires_at > statement_timestamp()\n            );\n        END\n        $$\n        ",
    "CREATE OR REPLACE FUNCTION idis_task6_immutable_guard() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "BEGIN IF TG_OP = 'INSERT' THEN IF NOT idis_task6_current_lease_is_valid( NEW.tenant_id, "
    "NEW.deal_id, NEW.run_id ) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001'; "
    "END IF; RETURN NEW; END IF; IF TG_OP = 'DELETE' AND idis_task6_erasure_requested(OLD.tenant_id, "
    "OLD.deal_id) THEN RETURN OLD; END IF; RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE' "
    "USING ERRCODE = '23514'; END $$",
    "CREATE OR REPLACE FUNCTION idis_task6_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "DECLARE current_tenant_id UUID; current_deal_id UUID; current_run_id UUID; current_owner_id "
    "UUID; current_fencing_token BIGINT; BEGIN IF TG_OP = 'DELETE' THEN IF "
    "idis_task6_erasure_requested(OLD.tenant_id, OLD.deal_id) THEN RETURN OLD; END IF; RAISE "
    "EXCEPTION 'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE' USING ERRCODE = '23514'; END IF; BEGIN "
    "current_tenant_id := NULLIF( current_setting('idis.worker_lease_tenant_id', true), '' )::uuid; "
    "current_deal_id := NULLIF( current_setting('idis.worker_lease_deal_id', true), '' )::uuid; "
    "current_run_id := NULLIF( current_setting('idis.worker_lease_run_id', true), '' )::uuid; "
    "current_owner_id := NULLIF( current_setting('idis.worker_lease_owner_id', true), '' )::uuid; "
    "current_fencing_token := NULLIF( current_setting('idis.worker_lease_fencing_token', true), '' "
    ")::bigint; EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = "
    "'40001'; END; IF NOT EXISTS ( SELECT 1 FROM runs r WHERE r.tenant_id = NEW.tenant_id AND "
    "r.deal_id = NEW.deal_id AND r.run_id = NEW.run_id AND r.status = 'RUNNING' AND r.tenant_id = "
    "current_tenant_id AND r.deal_id = current_deal_id AND r.run_id = current_run_id AND "
    "r.lease_owner_id = current_owner_id AND r.lease_fencing_token = current_fencing_token AND "
    "r.lease_expires_at > statement_timestamp() ) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING "
    "ERRCODE = '40001'; END IF; IF NOT EXISTS ( SELECT 1 FROM run_steps s WHERE s.tenant_id = "
    "NEW.tenant_id AND s.run_id = NEW.run_id AND s.step_id = NEW.run_step_id AND s.step_name = "
    "'METHODOLOGY_EXTRACTION_TASK_EXECUTION' AND s.step_order = 6 AND s.status = 'RUNNING' ) THEN "
    "RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001'; END IF; IF TG_OP = 'INSERT' THEN "
    "IF NEW.status <> 'STARTED' THEN RAISE EXCEPTION 'METHODOLOGY_ATTEMPT_MUST_START' USING ERRCODE = "
    "'23514'; END IF; IF NEW.lease_owner_id <> current_owner_id OR NEW.lease_fencing_token <> "
    "current_fencing_token THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001'; END "
    "IF; RETURN NEW; END IF; IF OLD.status <> 'STARTED' OR NEW.status NOT IN ('SUCCEEDED', 'FAILED', "
    "'OUTCOME_UNKNOWN') OR (to_jsonb(NEW) - ARRAY[ 'status', 'observed_model', 'provider_request_id', "
    "'provider_response_sha256', 'dispatched_call_sha256', 'completed_at', 'latency_ms', "
    "'input_tokens', 'output_tokens', 'stop_reason', 'budget_terminal_state', 'receipt_sha256', "
    "'safe_failure_code' ]::text[]) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY[ 'status', "
    "'observed_model', 'provider_request_id', 'provider_response_sha256', 'dispatched_call_sha256', "
    "'completed_at', 'latency_ms', 'input_tokens', 'output_tokens', 'stop_reason', "
    "'budget_terminal_state', 'receipt_sha256', 'safe_failure_code' ]::text[]) THEN RAISE EXCEPTION "
    "'METHODOLOGY_EXTRACTION_ATTEMPT_IMMUTABLE' USING ERRCODE = '23514'; END IF; IF NEW.status IN "
    "('SUCCEEDED', 'FAILED') AND ( current_owner_id <> OLD.lease_owner_id OR current_fencing_token <> "
    "OLD.lease_fencing_token ) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001'; "
    "END IF; IF NEW.status = 'OUTCOME_UNKNOWN' AND NOT ( ( current_owner_id = OLD.lease_owner_id AND "
    "current_fencing_token = OLD.lease_fencing_token ) OR current_fencing_token > "
    "OLD.lease_fencing_token ) THEN RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE = '40001'; "
    "END IF; RETURN NEW; END $$",
    "CREATE OR REPLACE FUNCTION idis_task6_validate_result_outputs() RETURNS trigger LANGUAGE plpgsql "
    "AS $$ DECLARE checked_result UUID; checked_outcome TEXT; output_count BIGINT; BEGIN "
    "checked_result := COALESCE(NEW.result_id, OLD.result_id); SELECT outcome INTO checked_outcome "
    "FROM methodology_extraction_results WHERE result_id = checked_result; IF checked_outcome IS NULL "
    "THEN RETURN NULL; END IF; SELECT count(*) INTO output_count FROM "
    "methodology_result_factual_outputs WHERE result_id = checked_result; IF (checked_outcome = "
    "'EVIDENCE_FOUND' AND output_count = 0) OR (checked_outcome = 'EVIDENCE_NOT_FOUND' AND "
    "output_count <> 0) THEN RAISE EXCEPTION 'METHODOLOGY_RESULT_FACTUAL_OUTPUT_MISMATCH' USING "
    "ERRCODE = '23514'; END IF; RETURN NULL; END $$",
    "DROP FUNCTION public.idis_task7_approved_erasure_context_v1(uuid,uuid)",
)

_HOLD_LOCK_UPGRADE_SQL_V1: Final = (
    "CREATE FUNCTION public.idis_task7_legal_hold_lock_guard() RETURNS trigger LANGUAGE plpgsql "
    "SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$DECLARE locked_tenant uuid; BEGIN IF "
    "TG_OP='UPDATE' AND NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN RAISE EXCEPTION "
    "'TASK7_ERASURE_HOLD_TENANT_CHANGE_REJECTED' USING ERRCODE='23514'; END IF; IF TG_OP='DELETE' "
    "THEN locked_tenant:=OLD.tenant_id; ELSE locked_tenant:=NEW.tenant_id; END IF; IF "
    "pg_catalog.current_setting('idis.tenant_id',true) IS DISTINCT FROM locked_tenant::text THEN "
    "RAISE EXCEPTION 'TASK7_ERASURE_HOLD_TENANT_CONTEXT_REJECTED' USING ERRCODE='42501'; END IF; "
    "PERFORM "
    "pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('idis-task7-erasure-tenant-lock-v1:'||locked_tenant::text,0)); "
    "IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END$$",
    "ALTER FUNCTION public.idis_task7_legal_hold_lock_guard() OWNER TO idis_erasure_function_owner",
    "REVOKE ALL ON FUNCTION public.idis_task7_legal_hold_lock_guard() FROM PUBLIC",
    "CREATE TRIGGER trg_task7_legal_holds_tenant_lock BEFORE INSERT OR UPDATE OR DELETE ON "
    "public.legal_holds FOR EACH ROW EXECUTE FUNCTION public.idis_task7_legal_hold_lock_guard()",
)

_HOLD_LOCK_DOWNGRADE_SQL_V1: Final = (
    "DROP TRIGGER trg_task7_legal_holds_tenant_lock ON public.legal_holds",
    "DROP FUNCTION public.idis_task7_legal_hold_lock_guard()",
)

TASK7_TABLES_V1 = (
    "deliverable_manifest_references",
    "document_parse_attempts",
    "run_applicability_decisions",
    "run_consumer_receipts",
    "run_expected_consumer_edges",
    "run_input_bindings",
    "run_output_objects",
    "run_step_outputs",
    "source_lineage_objects",
)

REVIEWED_CURRENT_DDL_TABLES_V1 = (
    "deliverable_manifest_references",
    "run_consumer_receipts",
    "run_expected_consumer_edges",
    "run_applicability_decisions",
    "run_step_outputs",
    "methodology_result_claim_links",
    "methodology_result_factual_outputs",
    "methodology_extraction_results",
    "methodology_extraction_tasks",
    "methodology_coverage_records",
    "calc_sanads",
    "deterministic_calculations",
    "layer2_ic_findings",
    "layer2_ic_challenges",
    "debate_sessions",
    "evidence_trust_findings",
    "validated_evidence_packages",
    "muhasabah_records",
    "runs",
    "deliverables",
    "vector_embeddings",
    "run_output_objects",
    "run_input_bindings",
    "document_spans",
    "data_room_package_files",
    "document_parse_attempts",
    "documents",
    "document_artifacts",
    "data_room_packages",
    "source_lineage_objects",
    "run_steps",
    "methodology_result_evidence_links",
    "methodology_result_sanad_links",
    "methodology_result_defect_links",
)

_ALTERED_TABLES = tuple(
    table for table in REVIEWED_CURRENT_DDL_TABLES_V1 if table not in TASK7_TABLES_V1
)
_SOURCE_NATIVE_TABLES = (
    "data_room_packages",
    "data_room_package_files",
    "document_artifacts",
    "documents",
    "document_parse_attempts",
    "document_spans",
)
_PREEXISTING_INDEXES = frozenset(
    {
        "idx_runs_tenant_id",
        "idx_runs_deal_id",
        "idx_run_steps_tenant_run",
        "idx_run_steps_tenant_run_order",
        "ux_claims_task6_scope",
        "ux_document_spans_task6_scope",
        "ux_sanads_task6_scope",
    }
)

CANONICAL_TENANT_PREDICATE = """\
NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL
AND tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid
"""

ERASURE_TENANT_ADVISORY_LOCK_CONTRACT_V1 = (
    "erasure-tenant-advisory-lock-v1",
    "PostgreSQL-16",
    "pg_catalog.hashtextextended",
    "idis-task7-erasure-tenant-lock-v1:",
    0,
    "pg_catalog.pg_advisory_xact_lock",
)

ERASURE_HOLD_ADVISORY_LOCK_CONTRACT_V1 = (
    "legal-hold-lock-enforcement-v1",
    "public.idis_task7_legal_hold_lock_guard()",
    "trg_task7_legal_holds_tenant_lock",
    "public.legal_holds",
    "BEFORE",
    "INSERT",
    "UPDATE",
    "DELETE",
    "ROW",
    "O",
    "PostgreSQL-16-trigger-acquires-xact-lock-v1",
    "TASK7_ERASURE_HOLD_TENANT_CONTEXT_REJECTED",
    "TASK7_ERASURE_HOLD_TENANT_CHANGE_REJECTED",
)

# Hand-auditable source markers.  Executable definitions are decoded from the
# raw-hash-pinned artifact below; these literals make accidental authority
# omission visible to source-only migration review.
_REVIEWED_TASK7_AUTHORITY_NAMES = """
ck_task7_deliverable_manifest_references_coordinate
ck_task7_deliverable_manifest_references_distinct_roots
ck_task7_deliverable_manifest_references_hashes
ck_task7_deliverable_manifest_references_lease
ck_task7_deliverable_manifest_references_member_kind
ck_task7_document_parse_attempts_binding_state
ck_task7_document_parse_attempts_hashes
ck_task7_document_parse_attempts_nonblank
ck_task7_document_parse_attempts_ordinal
ck_task7_document_parse_attempts_origin
ck_task7_document_parse_attempts_status
ck_task7_document_parse_attempts_times
ck_task7_run_applicability_decisions_hashes
ck_task7_run_applicability_decisions_lease
ck_task7_run_applicability_decisions_nonblank
ck_task7_run_applicability_decisions_outcome
ck_task7_run_applicability_decisions_phase
ck_task7_run_applicability_decisions_upstream_xor
ck_task7_run_consumer_receipts_lease
ck_task7_run_consumer_receipts_nonblank
ck_task7_run_consumer_receipts_usage_sha
ck_task7_run_expected_consumer_edges_hashes
ck_task7_run_expected_consumer_edges_lease
ck_task7_run_expected_consumer_edges_nonblank
ck_task7_run_expected_consumer_edges_origin
ck_task7_run_expected_consumer_edges_producer_xor
ck_task7_run_input_bindings_disposition
ck_task7_run_input_bindings_hashes
ck_task7_run_input_bindings_nonblank
ck_task7_run_input_bindings_purpose
ck_task7_run_input_bindings_recipe
ck_task7_run_input_bindings_source_kind
ck_task7_run_output_objects_class
ck_task7_run_output_objects_hashes
ck_task7_run_output_objects_lease
ck_task7_run_output_objects_nonblank
ck_task7_run_output_objects_origin
ck_task7_run_step_outputs_hashes
ck_task7_run_step_outputs_lease
ck_task7_run_step_outputs_nonblank
ck_task7_run_step_outputs_ordinal
ck_task7_source_lineage_objects_disposition
ck_task7_source_lineage_objects_hashes
ck_task7_source_lineage_objects_kind
ck_task7_source_lineage_objects_nonblank
ck_task7_source_lineage_objects_origin
fk_task7_deliverable_manifest_references_manifest
fk_task7_deliverable_manifest_references_manifest_root
fk_task7_deliverable_manifest_references_member_root
fk_task7_deliverable_manifest_references_member_step
fk_task7_deliverable_manifest_references_native_root
fk_task7_deliverable_manifest_references_run
fk_task7_document_parse_attempts_source_root
fk_task7_parse_attempts_document
fk_task7_run_applicability_decisions_deciding_output
fk_task7_run_applicability_decisions_input
fk_task7_run_applicability_decisions_output
fk_task7_run_applicability_decisions_run
fk_task7_run_consumer_receipts_consumer_output
fk_task7_run_consumer_receipts_edge
fk_task7_run_consumer_receipts_run
fk_task7_run_expected_consumer_edges_consumer_step
fk_task7_run_expected_consumer_edges_decision
fk_task7_run_expected_consumer_edges_input
fk_task7_run_expected_consumer_edges_output
fk_task7_run_expected_consumer_edges_run
fk_task7_run_input_bindings_corpus
fk_task7_run_input_bindings_run
fk_task7_run_input_bindings_source
fk_task7_run_output_objects_run
fk_task7_run_step_outputs_output
fk_task7_run_step_outputs_step
fk_task7_source_lineage_objects_corpus
fk_task7_source_lineage_objects_deal
ix_task7_deliverable_manifest_references_manifest_fk
ix_task7_deliverable_manifest_references_member_root_fk
ix_task7_deliverable_manifest_references_member_step_fk
ix_task7_deliverable_manifest_references_native_root_fk
ix_task7_deliverable_manifest_references_run_fk
ix_task7_document_parse_attempts_document_ordinal
ix_task7_document_parse_attempts_receipt
ix_task7_document_parse_attempts_source_root_scope
ix_task7_run_applicability_decisions_deciding
ix_task7_run_applicability_decisions_input
ix_task7_run_applicability_decisions_output
ix_task7_run_applicability_decisions_subject
ix_task7_run_consumer_receipts_consumer
ix_task7_run_consumer_receipts_consumer_output
ix_task7_run_consumer_receipts_edge_fk
ix_task7_run_expected_consumer_edges_consumer
ix_task7_run_expected_consumer_edges_decision
ix_task7_run_expected_consumer_edges_input
ix_task7_run_expected_consumer_edges_output
ix_task7_run_expected_consumer_edges_run_fk
ix_task7_run_input_bindings_corpus
ix_task7_run_input_bindings_run_fk
ix_task7_run_input_bindings_source_fk
ix_task7_run_output_objects_created
ix_task7_run_output_objects_kind
ix_task7_run_output_objects_output_sha
ix_task7_run_step_outputs_consumer_lookup
ix_task7_run_step_outputs_output_fk
ix_task7_run_step_outputs_step_fk
ix_task7_source_lineage_objects_content
ix_task7_source_lineage_objects_corpus_kind
pk_task7_deliverable_manifest_references
pk_task7_document_parse_attempts
pk_task7_run_applicability_decisions
pk_task7_run_consumer_receipts
pk_task7_run_expected_consumer_edges
pk_task7_run_input_bindings
pk_task7_run_output_objects
pk_task7_run_step_outputs
pk_task7_source_lineage_objects
uq_task7_deliverable_manifest_references_coordinate
uq_task7_deliverable_manifest_references_member
uq_task7_deliverable_manifest_references_native_root
uq_task7_deliverable_manifest_references_reference_sha
uq_task7_deliverable_manifest_references_scoped_id
uq_task7_document_parse_attempts_ordinal
uq_task7_document_parse_attempts_scope
uq_task7_document_parse_attempts_source_root
uq_task7_run_applicability_decisions_decision_sha
uq_task7_run_applicability_decisions_scoped_id
uq_task7_run_applicability_decisions_subject
uq_task7_run_consumer_receipts_edge_output
uq_task7_run_consumer_receipts_one_per_edge
uq_task7_run_consumer_receipts_scoped_id
uq_task7_run_expected_consumer_edges_consumer_tuple
uq_task7_run_expected_consumer_edges_natural
uq_task7_run_expected_consumer_edges_scoped_id
uq_task7_run_expected_consumer_edges_tuple_sha
uq_task7_run_input_bindings_binding_sha
uq_task7_run_input_bindings_run_source
uq_task7_run_input_bindings_scoped_id
uq_task7_run_output_objects_class_kind
uq_task7_run_output_objects_scope
uq_task7_run_output_objects_semantic_identity
uq_task7_run_output_objects_step_ref
uq_task7_run_output_objects_tenant_run_id
uq_task7_run_step_outputs_coordinate
uq_task7_run_step_outputs_output_object
uq_task7_run_step_outputs_receipt_tuple
uq_task7_run_step_outputs_scoped_id
uq_task7_run_step_outputs_scoped_output
uq_task7_source_lineage_objects_binding_parent
uq_task7_source_lineage_objects_corpus_scope_kind
uq_task7_source_lineage_objects_identity_version
uq_task7_source_lineage_objects_scope_kind
"""

_SOURCE_CONTRACT_MARKERS = """
ALTER TABLE data_room_packages ck_task7_data_room_packages_binding_state
ALTER TABLE data_room_package_files ck_task7_data_room_package_files_binding_state
ALTER TABLE document_artifacts ck_task7_document_artifacts_binding_state
ALTER TABLE documents ck_task7_documents_binding_state
ALTER TABLE document_parse_attempts ck_task7_document_parse_attempts_binding_state
ALTER TABLE document_spans ck_task7_document_spans_binding_state
ALTER TABLE data_room_packages ALTER COLUMN source_binding_state SET NOT NULL
ALTER TABLE data_room_package_files ALTER COLUMN source_binding_state SET NOT NULL
ALTER TABLE document_artifacts ALTER COLUMN source_binding_state SET NOT NULL
ALTER TABLE documents ALTER COLUMN source_binding_state SET NOT NULL
ALTER TABLE document_parse_attempts ALTER COLUMN source_binding_state SET NOT NULL
ALTER TABLE document_spans ALTER COLUMN source_binding_state SET NOT NULL
source_lineage_object_id source_lineage_object_kind source_corpus_receipt_id
parse_attempt_id source_binding_state write_origin BACKFILL RUNTIME
fk_task7_package_files_package fk_task7_package_files_document
fk_task7_documents_artifact fk_task7_spans_document fk_task7_spans_deal
fk_task7_spans_parse_attempt ux_task7_package_files_doc_id_bound
ux_task7_package_files_document_id_bound
data_room_package_files_package_id_fkey document_spans_document_id_fkey
ON DELETE NO ACTION legacy-open-freeze-v1 source-package-manifest-v1
TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE
"""

_ROOT_CONTRACT_MARKERS = """
ck_task7_methodology_coverage_records_
fk_task7_methodology_coverage_records_output_root
ix_task7_methodology_coverage_records_output_root_scope
ck_task7_methodology_extraction_tasks_
fk_task7_methodology_extraction_tasks_output_root
ix_task7_methodology_extraction_tasks_output_root_scope
ck_task7_methodology_extraction_results_
fk_task7_methodology_extraction_results_output_root
ix_task7_methodology_extraction_results_output_root_scope
ck_task7_methodology_result_factual_outputs_
fk_task7_methodology_result_factual_outputs_output_root
ix_task7_methodology_result_factual_outputs_output_root_scope
ck_task7_methodology_result_claim_links_
fk_task7_methodology_result_claim_links_output_root
ix_task7_methodology_result_claim_links_output_root_scope
ck_task7_methodology_result_evidence_links_
fk_task7_methodology_result_evidence_links_output_root
ix_task7_methodology_result_evidence_links_output_root_scope
ck_task7_methodology_result_sanad_links_
fk_task7_methodology_result_sanad_links_output_root
ix_task7_methodology_result_sanad_links_output_root_scope
ck_task7_methodology_result_defect_links_
fk_task7_methodology_result_defect_links_output_root
ix_task7_methodology_result_defect_links_output_root_scope
ck_task7_deterministic_calculations_
fk_task7_deterministic_calculations_output_root
ix_task7_deterministic_calculations_output_root_scope
ck_task7_vector_embeddings_
fk_task7_vector_embeddings_output_root
ix_task7_vector_embeddings_output_root_scope
ck_task7_evidence_trust_findings_
fk_task7_evidence_trust_findings_output_root
ix_task7_evidence_trust_findings_output_root_scope
ck_task7_muhasabah_records_
fk_task7_muhasabah_records_output_root
ix_task7_muhasabah_records_output_root_scope
ck_task7_validated_evidence_packages_
fk_task7_validated_evidence_packages_output_root
ix_task7_validated_evidence_packages_output_root_scope
ck_task7_debate_sessions_
fk_task7_debate_sessions_output_root
ix_task7_debate_sessions_output_root_scope
ck_task7_layer2_ic_challenges_
fk_task7_layer2_ic_challenges_output_root
ix_task7_layer2_ic_challenges_output_root_scope
ck_task7_layer2_ic_findings_
fk_task7_layer2_ic_findings_output_root
ix_task7_layer2_ic_findings_output_root_scope
ck_task7_deliverables_
fk_task7_deliverables_output_root
ix_task7_deliverables_output_root_scope
ALTER TABLE calc_sanads
ck_task7_calc_sanads_scope
run_output_object_id run_output_object_class run_output_object_kind NATIVE_ROW
ux_vector_embeddings_reusable ux_vector_embeddings_strict_run
ix_task7_calc_sanads_calculation_fk
ix_task7_deterministic_calculations_run_fk
ix_task7_vector_embeddings_run_fk
content_type size_bytes content_sha256 uri_sha256 object_version_hash
download_proof_hash catalog_ordinal
ck_task7_deliverables_terminal_root
pipeline_contract_version pipeline_contract_hash pipeline_resume_state pipeline_resume_error_code
semantic_rule_version semantic_rule_hash legacy_run_resume_unsafe historical_terminal non_resumable
fk_task7_runs_deal fk_task7_run_steps_run uq_task7_run_steps_order
"""

# The implementation intentionally loops over the closed table tuple.  These
# review literals preserve the source-only tripwire without duplicating DDL
# execution paths.
_TASK7_RLS_REVIEW_MARKERS = """
ALTER TABLE deliverable_manifest_references ENABLE ROW LEVEL SECURITY
ALTER TABLE deliverable_manifest_references FORCE ROW LEVEL SECURITY
CREATE POLICY deliverable_manifest_references_tenant_isolation USING marker WITH CHECK marker
ALTER TABLE document_parse_attempts ENABLE ROW LEVEL SECURITY
ALTER TABLE document_parse_attempts FORCE ROW LEVEL SECURITY
CREATE POLICY document_parse_attempts_tenant_isolation USING marker WITH CHECK marker
ALTER TABLE run_applicability_decisions ENABLE ROW LEVEL SECURITY
ALTER TABLE run_applicability_decisions FORCE ROW LEVEL SECURITY
CREATE POLICY run_applicability_decisions_tenant_isolation USING marker WITH CHECK marker
ALTER TABLE run_consumer_receipts ENABLE ROW LEVEL SECURITY
ALTER TABLE run_consumer_receipts FORCE ROW LEVEL SECURITY
CREATE POLICY run_consumer_receipts_tenant_isolation USING marker WITH CHECK marker
ALTER TABLE run_expected_consumer_edges ENABLE ROW LEVEL SECURITY
ALTER TABLE run_expected_consumer_edges FORCE ROW LEVEL SECURITY
CREATE POLICY run_expected_consumer_edges_tenant_isolation USING marker WITH CHECK marker
ALTER TABLE run_input_bindings ENABLE ROW LEVEL SECURITY
ALTER TABLE run_input_bindings FORCE ROW LEVEL SECURITY
CREATE POLICY run_input_bindings_tenant_isolation USING marker WITH CHECK marker
ALTER TABLE run_output_objects ENABLE ROW LEVEL SECURITY
ALTER TABLE run_output_objects FORCE ROW LEVEL SECURITY
CREATE POLICY run_output_objects_tenant_isolation USING marker WITH CHECK marker
ALTER TABLE run_step_outputs ENABLE ROW LEVEL SECURITY
ALTER TABLE run_step_outputs FORCE ROW LEVEL SECURITY
CREATE POLICY run_step_outputs_tenant_isolation USING marker WITH CHECK marker
ALTER TABLE source_lineage_objects ENABLE ROW LEVEL SECURITY
ALTER TABLE source_lineage_objects FORCE ROW LEVEL SECURITY
CREATE POLICY source_lineage_objects_tenant_isolation USING marker WITH CHECK marker
"""

_DOWNGRADE_REVIEW_MARKERS = (
    "RESTORE full-strict-v17 write_origin = 'RUNTIME' source_binding_state = 'UNBOUND'"
)

_LEGACY_GUARD_MARKERS = (
    "idis_task5_coverage_scope_guard",
    "idis_task6_immutable_guard",
)


if tuple(_DDL_ROWS) != REVIEWED_CURRENT_DDL_TABLES_V1:
    raise RuntimeError("TASK7_REVIEWED_DDL_TABLE_CENSUS_MISMATCH")

_SOURCE_LINEAGE_NAMESPACE = UUID("90a5de51-2753-5547-9375-ac5312629947")
_PARSE_ATTEMPT_NAMESPACE = UUID("6f591e1c-c7b1-5f0f-8ec8-c93380c6f6b1")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ALIAS_RE = re.compile(r"^(?:company|deal|documents|run|ledger|package)-[0-9]{3}$")
_UPLOAD_NAME_RE = re.compile(
    r"^(?P<company>company-[0-9]{3})-document-(?P<sequence>[0-9]{5})\."
    r"(?P<extension>[a-z0-9]{1,12})$"
)


def _normalize_json(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(key)): _normalize_json(item)
            for key, item in value.items()
        }
    if isinstance(value, float):
        raise RuntimeError("TASK7_CANONICAL_JSON_FLOAT_FORBIDDEN")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _normalize_json(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _rfc3339(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _deterministic_uuid(namespace: UUID, name_tuple: list[Any]) -> str:
    return str(uuid5(namespace, _sha256_json(name_tuple)))


def _source_lineage_id(
    *,
    tenant_id: str,
    deal_id: str,
    corpus_receipt_id: str,
    object_kind: str,
    native_scoped_id: Any,
    source_version: str,
    source_disposition: str,
    object_identity_sha256: str,
    content_sha256: str,
    corpus_receipt_sha256: str,
) -> str:
    return _deterministic_uuid(
        _SOURCE_LINEAGE_NAMESPACE,
        [
            "source-lineage-object-v2",
            tenant_id,
            deal_id,
            corpus_receipt_id,
            object_kind,
            native_scoped_id,
            source_version,
            source_disposition,
            object_identity_sha256,
            content_sha256,
            corpus_receipt_sha256,
        ],
    )


def _manifest_sha256(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("manifest_sha256", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_uuid(value: Any, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RuntimeError(f"TASK7_SOURCE_MANIFEST_UUID_INVALID:{field}") from exc


def _validate_source_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("TASK7_SOURCE_MANIFEST_OBJECT_REQUIRED")
    raw_files = value.get("files")
    manifest: dict[str, Any] = {
        **value,
        "files": (
            [dict(row) if isinstance(row, dict) else row for row in raw_files]
            if isinstance(raw_files, list)
            else raw_files
        ),
    }
    value = manifest
    expected_keys = {
        "schema_version",
        "company_alias",
        "deal_alias",
        "document_set_alias",
        "run_alias",
        "ledger_alias",
        "package_alias",
        "source_tree_fingerprint",
        "file_count",
        "total_bytes",
        "files",
        "manifest_sha256",
    }
    if set(value) != expected_keys:
        raise RuntimeError("TASK7_SOURCE_MANIFEST_FIELDS_INVALID")
    if value["schema_version"] != "source-package-manifest-v1":
        raise RuntimeError("TASK7_SOURCE_MANIFEST_VERSION_INVALID")

    aliases = (
        "company_alias",
        "deal_alias",
        "document_set_alias",
        "run_alias",
        "ledger_alias",
        "package_alias",
    )
    if any(
        not isinstance(value[field], str) or _SAFE_ALIAS_RE.fullmatch(value[field]) is None
        for field in aliases
    ):
        raise RuntimeError("TASK7_SOURCE_MANIFEST_ALIAS_INVALID")
    suffixes = {str(value[field]).rsplit("-", 1)[-1] for field in aliases}
    if len(suffixes) != 1:
        raise RuntimeError("TASK7_SOURCE_MANIFEST_ALIAS_SCOPE_MISMATCH")

    files = value["files"]
    if not isinstance(files, list) or not files:
        raise RuntimeError("TASK7_SOURCE_MANIFEST_FILES_INVALID")
    if type(value["file_count"]) is not int or value["file_count"] != len(files):
        raise RuntimeError("TASK7_SOURCE_MANIFEST_FILE_COUNT_INVALID")
    if type(value["total_bytes"]) is not int or value["total_bytes"] < 0:
        raise RuntimeError("TASK7_SOURCE_MANIFEST_TOTAL_BYTES_INVALID")
    for field in ("source_tree_fingerprint", "manifest_sha256"):
        if not isinstance(value[field], str) or _SHA256_RE.fullmatch(value[field]) is None:
            raise RuntimeError(f"TASK7_SOURCE_MANIFEST_HASH_INVALID:{field}")

    expected_file_keys = {
        "sequence",
        "generated_upload_name",
        "source_locator_sha256",
        "content_sha256",
        "size_bytes",
        "extension",
        "document_id",
        "doc_id",
    }
    unique_fields: dict[str, set[Any]] = {
        "generated_upload_name": set(),
        "source_locator_sha256": set(),
        "content_sha256": set(),
        "document_id": set(),
        "doc_id": set(),
    }
    total_bytes = 0
    for ordinal, row in enumerate(files):
        if not isinstance(row, dict) or set(row) != expected_file_keys:
            raise RuntimeError("TASK7_SOURCE_MANIFEST_FILE_FIELDS_INVALID")
        if type(row["sequence"]) is not int or row["sequence"] != ordinal:
            raise RuntimeError("TASK7_SOURCE_MANIFEST_SEQUENCE_INVALID")
        if type(row["size_bytes"]) is not int or row["size_bytes"] < 0:
            raise RuntimeError("TASK7_SOURCE_MANIFEST_FILE_SIZE_INVALID")
        total_bytes += row["size_bytes"]
        for field in ("source_locator_sha256", "content_sha256"):
            if not isinstance(row[field], str) or _SHA256_RE.fullmatch(row[field]) is None:
                raise RuntimeError(f"TASK7_SOURCE_MANIFEST_FILE_HASH_INVALID:{field}")
        if (
            not isinstance(row["extension"], str)
            or re.fullmatch(r"[a-z0-9]{1,12}", row["extension"]) is None
        ):
            raise RuntimeError("TASK7_SOURCE_MANIFEST_EXTENSION_INVALID")
        name = row["generated_upload_name"]
        match = _UPLOAD_NAME_RE.fullmatch(name) if isinstance(name, str) else None
        if (
            match is None
            or match.group("company") != value["company_alias"]
            or int(match.group("sequence")) != ordinal + 1
            or match.group("extension") != row["extension"]
        ):
            raise RuntimeError("TASK7_SOURCE_MANIFEST_UPLOAD_NAME_INVALID")
        row["document_id"] = _require_uuid(row["document_id"], field="document_id")
        row["doc_id"] = _require_uuid(row["doc_id"], field="doc_id")
        for field, seen in unique_fields.items():
            if row[field] in seen:
                raise RuntimeError(f"TASK7_SOURCE_MANIFEST_FILE_DUPLICATE:{field}")
            seen.add(row[field])
    if total_bytes != value["total_bytes"]:
        raise RuntimeError("TASK7_SOURCE_MANIFEST_TOTAL_BYTES_MISMATCH")
    if _manifest_sha256(value) != value["manifest_sha256"]:
        raise RuntimeError("TASK7_SOURCE_MANIFEST_SELF_HASH_INVALID")
    return manifest


def _split_named(authority: str) -> tuple[str, str, str]:
    opening = authority.find("(")
    closing = authority.rfind(")")
    if opening <= 0 or closing <= opening:
        raise RuntimeError(f"TASK7_DDL_AUTHORITY_INVALID:{authority}")
    return authority[:opening], authority[opening + 1 : closing], authority[closing + 1 :].strip()


def _constraint_exists(table: str, name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conrelid=CAST(:table AS regclass) AND conname=:name)"
            ),
            {"table": table, "name": name},
        )
        .scalar_one()
    )


def _index_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE n.nspname='public' "
                "AND c.relkind='i' AND c.relname=:name)"
            ),
            {"name": name},
        )
        .scalar_one()
    )


def _column_exists(table: str, column: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:table AND column_name=:column)"
            ),
            {"table": table, "column": column},
        )
        .scalar_one()
    )


def _add_column(table: str, definition: str, *, initially_nullable: bool = False) -> None:
    column = definition.split(None, 1)[0]
    if _column_exists(table, column):
        return
    executable = (
        re.sub(r"\s+NOT NULL\b", "", definition, count=1) if initially_nullable else definition
    )
    op.execute(f"ALTER TABLE {table} ADD COLUMN {executable}")


def _create_reviewed_tables() -> None:
    def create(table: str, prefix: str) -> None:
        columns = ",\n".join(_DDL_ROWS[table]["column_tuple"])
        op.execute(f"{prefix} (\n{columns}\n)")

    create("source_lineage_objects", "CREATE TABLE source_lineage_objects")
    create("document_parse_attempts", "CREATE TABLE document_parse_attempts")
    create("run_output_objects", "CREATE TABLE run_output_objects")
    create("run_input_bindings", "CREATE TABLE run_input_bindings")
    create("run_step_outputs", "CREATE TABLE run_step_outputs")
    create("run_applicability_decisions", "CREATE TABLE run_applicability_decisions")
    create("run_expected_consumer_edges", "CREATE TABLE run_expected_consumer_edges")
    create("run_consumer_receipts", "CREATE TABLE run_consumer_receipts")
    create(
        "deliverable_manifest_references",
        "CREATE TABLE deliverable_manifest_references",
    )


def _uuid_text(value: Any, *, field: str) -> str:
    return _require_uuid(value, field=field)


def _safe_code_set(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or re.fullmatch(r"[a-z][a-z0-9_]*", item) is None
        for item in value
    ):
        raise RuntimeError(f"TASK7_SOURCE_TERMINAL_STATE_INVALID:{field}")
    return sorted(set(value))


def _json_object(value: Any, *, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(code)
    return value


def _terminal_document_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    support = metadata.get("parser_support_status")
    triage = metadata.get("parser_triage_status")
    if support != "supported" or triage != "ready":
        raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:parser_projection")

    raw_refs = metadata.get("applicable_schema_refs", [])
    if not isinstance(raw_refs, list):
        raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:applicable_schema_refs")
    refs: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, str, str]] = set()
    for raw_ref in raw_refs:
        ref = _json_object(
            raw_ref,
            code="TASK7_SOURCE_TERMINAL_STATE_INVALID:applicable_schema_ref",
        )
        if set(ref) != {
            "schema_id",
            "schema_version",
            "schema_sha256",
            "validated_payload",
        }:
            raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:applicable_schema_ref")
        key = (
            str(ref["schema_id"]),
            str(ref["schema_version"]),
            str(ref["schema_sha256"]),
        )
        if not key[0] or not key[1] or _SHA256_RE.fullmatch(key[2]) is None or key in seen_refs:
            raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:applicable_schema_ref")
        _canonical_json_bytes(ref["validated_payload"])
        seen_refs.add(key)
        refs.append(
            {
                "schema_id": key[0],
                "schema_version": key[1],
                "schema_sha256": key[2],
                "validated_payload": ref["validated_payload"],
            }
        )
    return {
        "parser_support_status": support,
        "parser_triage_status": triage,
        "applicable_schema_refs": refs,
    }


def _durable_object_integrity(
    metadata: dict[str, Any],
    *,
    doc_id: str,
    content_sha256: str,
    size_bytes: int,
    uri: str,
) -> dict[str, Any]:
    value = _json_object(
        metadata.get("durable_object_integrity"),
        code="TASK7_SOURCE_TERMINAL_STATE_INVALID:durable_object_integrity",
    )
    expected_keys = {
        "schema_version",
        "doc_id",
        "content_sha256",
        "size_bytes",
        "uri_sha256",
        "version_id",
        "backend",
        "streaming_verified",
    }
    if set(value) != expected_keys:
        raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:durable_object_integrity")
    normalized = {
        **value,
        "doc_id": _uuid_text(value["doc_id"], field="durable_object_integrity.doc_id"),
        "version_id": _uuid_text(value["version_id"], field="durable_object_integrity.version_id"),
    }
    if (
        normalized["schema_version"] != "durable-object-integrity-v1"
        or normalized["doc_id"] != doc_id
        or normalized["content_sha256"] != content_sha256
        or normalized["size_bytes"] != size_bytes
        or normalized["uri_sha256"] != hashlib.sha256(uri.encode("utf-8")).hexdigest()
        or not isinstance(normalized["backend"], str)
        or not normalized["backend"].startswith("compliant:")
        or normalized["streaming_verified"] is not True
    ):
        raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:durable_object_integrity")
    return normalized


def _validated_span_locator(span_type: str, value: Any) -> dict[str, Any]:
    locator = _json_object(
        value,
        code="TASK7_SOURCE_TERMINAL_STATE_INVALID:span_locator",
    )
    if span_type == "PARAGRAPH":
        if set(locator) != {"paragraph"} or type(locator["paragraph"]) is not int:
            raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span_locator")
        if locator["paragraph"] < 0:
            raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span_locator")
    elif span_type == "CELL":
        if set(locator) == {"table", "row", "col"}:
            if any(type(locator[key]) is not int or locator[key] < 0 for key in locator):
                raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span_locator")
        elif set(locator) == {"sheet", "cell"}:
            if any(not isinstance(locator[key], str) or not locator[key] for key in locator):
                raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span_locator")
        else:
            raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span_locator")
    elif span_type == "TIMECODE":
        if set(locator) != {"start_ms", "end_ms"} or any(
            type(locator[key]) is not int or locator[key] < 0 for key in locator
        ):
            raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span_locator")
        if locator["end_ms"] < locator["start_ms"]:
            raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span_locator")
    elif span_type == "PAGE_TEXT":
        page = locator.get("page")
        if type(page) is not int or page < 1:
            raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span_locator")
        _canonical_json_bytes(locator)
    else:
        raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span_type")
    return locator


def _root_row(
    *,
    tenant_id: str,
    deal_id: str,
    package_id: str,
    object_kind: str,
    native_scoped_id: list[str],
    source_version: str,
    source_disposition: str,
    object_identity_sha256: str,
    content_sha256: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    root_id = _source_lineage_id(
        tenant_id=tenant_id,
        deal_id=deal_id,
        corpus_receipt_id=package_id,
        object_kind=object_kind,
        native_scoped_id=native_scoped_id,
        source_version=source_version,
        source_disposition=source_disposition,
        object_identity_sha256=object_identity_sha256,
        content_sha256=content_sha256,
        corpus_receipt_sha256=manifest_sha256,
    )
    return {
        "source_lineage_object_id": root_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "corpus_receipt_id": package_id,
        "object_kind": object_kind,
        "source_version": source_version,
        "source_disposition": source_disposition,
        "object_identity_sha256": object_identity_sha256,
        "content_sha256": content_sha256,
        "corpus_receipt_sha256": manifest_sha256,
        "write_origin": "BACKFILL",
    }


def _source_rows(statement: str) -> list[dict[str, Any]]:
    return [dict(row) for row in op.get_bind().execute(sa.text(statement)).mappings()]


def _plan_source_backfill_before_mutation() -> dict[str, Any]:
    packages = _source_rows(
        "SELECT package_id,tenant_id,deal_id,status,metadata FROM data_room_packages "
        "ORDER BY tenant_id,deal_id,package_id"
    )
    files = _source_rows(
        "SELECT file_entry_id,tenant_id,package_id,deal_id,sequence,path_hash,extension,"
        "sha256,file_status,support_status,triage_status,parse_status,reason_codes,"
        "error_codes,doc_id,document_id,storage_uri FROM data_room_package_files "
        "ORDER BY tenant_id,deal_id,package_id,sequence,file_entry_id"
    )
    artifacts = _source_rows(
        "SELECT doc_id,tenant_id,deal_id,doc_type,title,source_system,version_id,"
        "ingested_at,sha256,uri,metadata FROM document_artifacts "
        "ORDER BY tenant_id,deal_id,doc_id"
    )
    documents = _source_rows(
        "SELECT document_id,tenant_id,deal_id,doc_id,doc_type,parse_status,metadata,"
        "created_at,updated_at FROM documents ORDER BY tenant_id,deal_id,document_id"
    )
    spans = _source_rows(
        "SELECT span_id,tenant_id,deal_id,document_id,span_type,locator,text_excerpt,"
        "content_hash FROM document_spans ORDER BY tenant_id,deal_id,document_id,span_id"
    )
    if not any((packages, files, artifacts, documents, spans)):
        return {"roots": [], "attempts": [], "bindings": []}
    if not packages:
        raise RuntimeError("TASK7_SOURCE_BACKFILL_UNRECONSTRUCTIBLE")

    files_by_package: dict[str, list[dict[str, Any]]] = {}
    artifacts_by_id: dict[str, list[dict[str, Any]]] = {}
    documents_by_id: dict[str, list[dict[str, Any]]] = {}
    spans_by_document: dict[str, list[dict[str, Any]]] = {}
    for row in files:
        files_by_package.setdefault(str(row["package_id"]), []).append(row)
    for row in artifacts:
        artifacts_by_id.setdefault(str(row["doc_id"]), []).append(row)
    for row in documents:
        documents_by_id.setdefault(str(row["document_id"]), []).append(row)
    for row in spans:
        spans_by_document.setdefault(str(row["document_id"]), []).append(row)

    roots: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    consumed_files: set[str] = set()
    consumed_artifacts: set[str] = set()
    consumed_documents: set[str] = set()
    consumed_spans: set[str] = set()

    for package in packages:
        tenant_id = _uuid_text(package["tenant_id"], field="package.tenant_id")
        deal_id = _uuid_text(package["deal_id"], field="package.deal_id")
        package_id = _uuid_text(package["package_id"], field="package.package_id")
        if package["status"] not in {"OPEN", "SEALED"}:
            raise RuntimeError("TASK7_SOURCE_BACKFILL_UNRECONSTRUCTIBLE")
        package_metadata = _json_object(
            package["metadata"], code="TASK7_SOURCE_BACKFILL_UNRECONSTRUCTIBLE"
        )
        try:
            manifest = _validate_source_manifest(package_metadata.get("source_manifest"))
        except RuntimeError as exc:
            raise RuntimeError("TASK7_SOURCE_BACKFILL_UNRECONSTRUCTIBLE") from exc
        manifest_sha256 = str(manifest["manifest_sha256"])
        package_files = files_by_package.get(package_id, [])
        if len(package_files) != len(manifest["files"]):
            raise RuntimeError("TASK7_SOURCE_PACKAGE_MEMBERSHIP_UNRESOLVED")
        by_sequence = {row["sequence"]: row for row in package_files}
        if len(by_sequence) != len(package_files):
            raise RuntimeError("TASK7_SOURCE_PACKAGE_MEMBERSHIP_UNRESOLVED")

        package_members: list[list[Any]] = []
        package_binding_rows: list[dict[str, Any]] = []
        for manifest_file in manifest["files"]:
            file_row = by_sequence.get(manifest_file["sequence"])
            if file_row is None:
                raise RuntimeError("TASK7_SOURCE_PACKAGE_MEMBERSHIP_UNRESOLVED")
            file_entry_id = _uuid_text(file_row["file_entry_id"], field="file.file_entry_id")
            document_id = _uuid_text(file_row["document_id"], field="file.document_id")
            doc_id = _uuid_text(file_row["doc_id"], field="file.doc_id")
            if (
                _uuid_text(file_row["tenant_id"], field="file.tenant_id") != tenant_id
                or _uuid_text(file_row["deal_id"], field="file.deal_id") != deal_id
                or _uuid_text(file_row["package_id"], field="file.package_id") != package_id
                or file_row["path_hash"] != manifest_file["source_locator_sha256"]
                or file_row["extension"] != manifest_file["extension"]
                or file_row["sha256"] != manifest_file["content_sha256"]
                or document_id != manifest_file["document_id"]
                or doc_id != manifest_file["doc_id"]
            ):
                raise RuntimeError("TASK7_SOURCE_PACKAGE_MEMBERSHIP_UNRESOLVED")
            artifact_matches = artifacts_by_id.get(doc_id, [])
            document_matches = documents_by_id.get(document_id, [])
            if len(artifact_matches) != 1 or len(document_matches) != 1:
                raise RuntimeError("TASK7_SOURCE_PACKAGE_MEMBERSHIP_UNRESOLVED")
            artifact = artifact_matches[0]
            document = document_matches[0]
            if (
                _uuid_text(artifact["tenant_id"], field="artifact.tenant_id") != tenant_id
                or _uuid_text(artifact["deal_id"], field="artifact.deal_id") != deal_id
                or _uuid_text(document["tenant_id"], field="document.tenant_id") != tenant_id
                or _uuid_text(document["deal_id"], field="document.deal_id") != deal_id
                or _uuid_text(document["doc_id"], field="document.doc_id") != doc_id
            ):
                raise RuntimeError("TASK7_SOURCE_PACKAGE_MEMBERSHIP_UNRESOLVED")
            if artifact["sha256"] != manifest_file["content_sha256"] or not artifact["uri"]:
                raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:artifact")

            artifact_metadata = _json_object(
                artifact["metadata"],
                code="TASK7_SOURCE_TERMINAL_STATE_INVALID:artifact_metadata",
            )
            integrity = _durable_object_integrity(
                artifact_metadata,
                doc_id=doc_id,
                content_sha256=str(artifact["sha256"]),
                size_bytes=int(manifest_file["size_bytes"]),
                uri=str(artifact["uri"]),
            )
            document_metadata = _json_object(
                document["metadata"],
                code="TASK7_SOURCE_TERMINAL_STATE_INVALID:document_metadata",
            )
            parser_name = document_metadata.get("parser_runtime")
            if not isinstance(parser_name, str) or not parser_name.strip():
                raise RuntimeError("TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE")
            if (
                document["parse_status"] != "PARSED"
                or document_metadata.get("parser_runtime_status") != "completed"
                or _safe_code_set(
                    document_metadata.get("parse_warning_codes"),
                    field="parse_warning_codes",
                )
                or _safe_code_set(
                    document_metadata.get("parse_error_codes"),
                    field="parse_error_codes",
                )
            ):
                raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:document")
            terminal_metadata = _terminal_document_metadata(document_metadata)
            reason_codes = _safe_code_set(
                document_metadata.get("parser_reason_codes"), field="parser_reason_codes"
            )
            error_codes = _safe_code_set(
                document_metadata.get("parse_error_codes"), field="parse_error_codes"
            )
            if (
                file_row["file_status"] != "supported"
                or file_row["support_status"] != "supported"
                or file_row["triage_status"] != "ready"
                or file_row["parse_status"] != "PARSED"
                or file_row["reason_codes"] != reason_codes
                or file_row["error_codes"] != error_codes
            ):
                raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:package_projection")

            started_at = document["created_at"]
            finished_at = document["updated_at"]
            if (
                not isinstance(started_at, datetime)
                or not isinstance(finished_at, datetime)
                or finished_at < started_at
            ):
                raise RuntimeError("TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE")
            receipt_sha256 = _sha256_json(
                [
                    "terminal-document-metadata-v2",
                    document["doc_type"],
                    document["parse_status"],
                    terminal_metadata,
                ]
            )
            parser_version = "legacy-document-metadata-v1"
            parse_attempt_id = _deterministic_uuid(
                _PARSE_ATTEMPT_NAMESPACE,
                [
                    "document-parse-attempt-v1",
                    tenant_id,
                    deal_id,
                    document_id,
                    1,
                    parser_name,
                    parser_version,
                    artifact["sha256"],
                    receipt_sha256,
                    "PARSED",
                ],
            )

            document_spans = spans_by_document.get(document_id, [])
            if not document_spans:
                raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:no_evidence_spans")
            span_plans: list[dict[str, Any]] = []
            for span in document_spans:
                span_id = _uuid_text(span["span_id"], field="span.span_id")
                if (
                    _uuid_text(span["tenant_id"], field="span.tenant_id") != tenant_id
                    or _uuid_text(span["deal_id"], field="span.deal_id") != deal_id
                    or not isinstance(span["text_excerpt"], str)
                    or not span["text_excerpt"].strip()
                    or span["content_hash"]
                    != hashlib.sha256(span["text_excerpt"].encode("utf-8")).hexdigest()
                ):
                    raise RuntimeError("TASK7_SOURCE_TERMINAL_STATE_INVALID:span")
                locator = _validated_span_locator(str(span["span_type"]), span["locator"])
                locator_sha256 = _sha256_json(["span-locator-v1", span["span_type"], locator])
                span_identity_sha256 = _sha256_json(
                    [
                        "document-span-identity-v1",
                        span_id,
                        document_id,
                        parse_attempt_id,
                        span["span_type"],
                        locator_sha256,
                        span["content_hash"],
                    ]
                )
                span_version = f"document-span-v1:{parse_attempt_id}:{span['span_type']}"
                span_root = _root_row(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    package_id=package_id,
                    object_kind="SPAN",
                    native_scoped_id=[tenant_id, deal_id, document_id, span_id],
                    source_version=span_version,
                    source_disposition="EVIDENCE_BEARING",
                    object_identity_sha256=span_identity_sha256,
                    content_sha256=str(span["content_hash"]),
                    manifest_sha256=manifest_sha256,
                )
                roots.append(span_root)
                span_plans.append(
                    {
                        "table": "document_spans",
                        "native_id": span_id,
                        "root_id": span_root["source_lineage_object_id"],
                        "package_id": package_id,
                        "parse_attempt_id": parse_attempt_id,
                    }
                )
                consumed_spans.add(span_id)

            artifact_metadata_sha256 = _sha256_json(
                [
                    "document-artifact-metadata-v2",
                    hashlib.sha256(str(artifact["title"]).encode("utf-8")).hexdigest(),
                    artifact["source_system"],
                    artifact["doc_type"],
                    artifact["version_id"],
                    integrity,
                ]
            )
            artifact_identity_sha256 = _sha256_json(
                [
                    "document-artifact-identity-v1",
                    doc_id,
                    artifact["doc_type"],
                    artifact["source_system"],
                    artifact["version_id"],
                    _rfc3339(artifact["ingested_at"]),
                    artifact["sha256"],
                    hashlib.sha256(str(artifact["uri"]).encode("utf-8")).hexdigest(),
                    artifact_metadata_sha256,
                ]
            )
            artifact_version = f"document-artifact-v1:{artifact['version_id']}"
            artifact_root = _root_row(
                tenant_id=tenant_id,
                deal_id=deal_id,
                package_id=package_id,
                object_kind="ARTIFACT",
                native_scoped_id=[tenant_id, deal_id, doc_id],
                source_version=artifact_version,
                source_disposition="EVIDENCE_BEARING",
                object_identity_sha256=artifact_identity_sha256,
                content_sha256=str(artifact["sha256"]),
                manifest_sha256=manifest_sha256,
            )
            parse_attempt_identity_sha256 = _sha256_json(
                [
                    "parse-attempt-identity-v1",
                    parse_attempt_id,
                    document_id,
                    1,
                    parser_name,
                    parser_version,
                    artifact["sha256"],
                    receipt_sha256,
                    "PARSED",
                    _rfc3339(started_at),
                    _rfc3339(finished_at),
                ]
            )
            parse_attempt_version = f"parser-attempt-v1:{parser_name}:{parser_version}"
            parse_attempt_root = _root_row(
                tenant_id=tenant_id,
                deal_id=deal_id,
                package_id=package_id,
                object_kind="PARSE_ATTEMPT",
                native_scoped_id=[tenant_id, deal_id, document_id, parse_attempt_id],
                source_version=parse_attempt_version,
                source_disposition="EVIDENCE_BEARING",
                object_identity_sha256=parse_attempt_identity_sha256,
                content_sha256=str(artifact["sha256"]),
                manifest_sha256=manifest_sha256,
            )
            document_identity_sha256 = _sha256_json(
                [
                    "parsed-document-identity-v1",
                    document_id,
                    doc_id,
                    document["doc_type"],
                    document["parse_status"],
                    receipt_sha256,
                    parse_attempt_id,
                    receipt_sha256,
                ]
            )
            document_version = f"parsed-document-v1:{receipt_sha256}"
            document_root = _root_row(
                tenant_id=tenant_id,
                deal_id=deal_id,
                package_id=package_id,
                object_kind="DOCUMENT",
                native_scoped_id=[tenant_id, deal_id, document_id],
                source_version=document_version,
                source_disposition="EVIDENCE_BEARING",
                object_identity_sha256=document_identity_sha256,
                content_sha256=str(artifact["sha256"]),
                manifest_sha256=manifest_sha256,
            )
            storage_uri_sha256 = (
                None
                if file_row["storage_uri"] is None
                else hashlib.sha256(str(file_row["storage_uri"]).encode("utf-8")).hexdigest()
            )
            file_identity_sha256 = _sha256_json(
                [
                    "source-package-file-identity-v1",
                    package_id,
                    file_entry_id,
                    file_row["sequence"],
                    file_row["path_hash"],
                    file_row["extension"],
                    file_row["sha256"],
                    manifest_file["size_bytes"],
                    document_id,
                    doc_id,
                    file_row["file_status"],
                    file_row["support_status"],
                    file_row["triage_status"],
                    file_row["parse_status"],
                    reason_codes,
                    error_codes,
                    storage_uri_sha256,
                    "EVIDENCE_BEARING",
                    receipt_sha256,
                ]
            )
            file_version = f"source-package-file-v1:{manifest_sha256}:{file_row['sequence']}"
            file_root = _root_row(
                tenant_id=tenant_id,
                deal_id=deal_id,
                package_id=package_id,
                object_kind="PACKAGE_FILE",
                native_scoped_id=[tenant_id, deal_id, package_id, file_entry_id],
                source_version=file_version,
                source_disposition="EVIDENCE_BEARING",
                object_identity_sha256=file_identity_sha256,
                content_sha256=str(file_row["sha256"]),
                manifest_sha256=manifest_sha256,
            )
            roots.extend((file_root, artifact_root, document_root, parse_attempt_root))
            attempts.append(
                {
                    "parse_attempt_id": parse_attempt_id,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "document_id": document_id,
                    "attempt_ordinal": 1,
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                    "input_sha256": artifact["sha256"],
                    "receipt_sha256": receipt_sha256,
                    "status": "PARSED",
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "source_binding_state": "BOUND",
                    "source_lineage_object_id": parse_attempt_root["source_lineage_object_id"],
                    "source_corpus_receipt_id": package_id,
                    "write_origin": "BACKFILL",
                }
            )
            package_binding_rows.extend(
                [
                    {
                        "table": "data_room_package_files",
                        "native_id": file_entry_id,
                        "root_id": file_root["source_lineage_object_id"],
                        "package_id": package_id,
                    },
                    {
                        "table": "document_artifacts",
                        "native_id": doc_id,
                        "root_id": artifact_root["source_lineage_object_id"],
                        "package_id": package_id,
                    },
                    {
                        "table": "documents",
                        "native_id": document_id,
                        "root_id": document_root["source_lineage_object_id"],
                        "package_id": package_id,
                    },
                    *span_plans,
                ]
            )
            package_members.append(
                [
                    manifest_file["sequence"],
                    manifest_file["source_locator_sha256"],
                    manifest_file["content_sha256"],
                    manifest_file["size_bytes"],
                    manifest_file["extension"],
                    document_id,
                    doc_id,
                ]
            )
            consumed_files.add(file_entry_id)
            consumed_artifacts.add(doc_id)
            consumed_documents.add(document_id)

        package_identity_sha256 = _sha256_json(
            [
                "source-package-identity-v1",
                package_id,
                manifest["schema_version"],
                manifest_sha256,
                manifest["source_tree_fingerprint"],
                manifest["file_count"],
                manifest["total_bytes"],
                package_members,
            ]
        )
        package_version_prefix = (
            "source-package-manifest-v1"
            if package["status"] == "SEALED"
            else "legacy-open-freeze-v1"
        )
        package_root = _root_row(
            tenant_id=tenant_id,
            deal_id=deal_id,
            package_id=package_id,
            object_kind="PACKAGE",
            native_scoped_id=[tenant_id, deal_id, package_id],
            source_version=f"{package_version_prefix}:{manifest_sha256}",
            source_disposition="CORPUS",
            object_identity_sha256=package_identity_sha256,
            content_sha256=manifest_sha256,
            manifest_sha256=manifest_sha256,
        )
        roots.append(package_root)
        bindings.append(
            {
                "table": "data_room_packages",
                "native_id": package_id,
                "root_id": package_root["source_lineage_object_id"],
                "package_id": package_id,
                "seal_on_upgrade": package["status"] == "OPEN",
            }
        )
        bindings.extend(package_binding_rows)

    if (
        consumed_files != {str(row["file_entry_id"]) for row in files}
        or consumed_artifacts != {str(row["doc_id"]) for row in artifacts}
        or consumed_documents != {str(row["document_id"]) for row in documents}
        or consumed_spans != {str(row["span_id"]) for row in spans}
    ):
        raise RuntimeError("TASK7_SOURCE_PACKAGE_MEMBERSHIP_UNRESOLVED")
    return {"roots": roots, "attempts": attempts, "bindings": bindings}


def _apply_source_backfill(plan: dict[str, Any]) -> None:
    bind = op.get_bind()
    roots = plan["roots"]
    if roots:
        bind.execute(
            sa.text(
                "INSERT INTO source_lineage_objects (source_lineage_object_id,tenant_id,"
                "deal_id,corpus_receipt_id,object_kind,source_version,source_disposition,"
                "object_identity_sha256,content_sha256,corpus_receipt_sha256,write_origin) "
                "VALUES (CAST(:source_lineage_object_id AS uuid),CAST(:tenant_id AS uuid),"
                "CAST(:deal_id AS uuid),CAST(:corpus_receipt_id AS uuid),:object_kind,"
                ":source_version,:source_disposition,:object_identity_sha256,:content_sha256,"
                ":corpus_receipt_sha256,:write_origin)"
            ),
            roots,
        )
    attempts = plan["attempts"]
    if attempts:
        bind.execute(
            sa.text(
                "INSERT INTO document_parse_attempts (parse_attempt_id,tenant_id,deal_id,"
                "document_id,attempt_ordinal,parser_name,parser_version,input_sha256,"
                "receipt_sha256,status,started_at,finished_at,source_binding_state,"
                "source_lineage_object_id,source_corpus_receipt_id,write_origin) VALUES ("
                "CAST(:parse_attempt_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),"
                "CAST(:document_id AS uuid),:attempt_ordinal,:parser_name,:parser_version,"
                ":input_sha256,:receipt_sha256,:status,:started_at,:finished_at,"
                ":source_binding_state,CAST(:source_lineage_object_id AS uuid),"
                "CAST(:source_corpus_receipt_id AS uuid),:write_origin)"
            ),
            attempts,
        )
    update_sql = {
        "data_room_packages": (
            "UPDATE data_room_packages SET status=CASE WHEN :seal_on_upgrade THEN 'SEALED' "
            "ELSE status END,source_binding_state='BOUND',"
            "source_lineage_object_id=CAST(:root_id AS uuid) "
            "WHERE package_id=CAST(:native_id AS uuid)"
        ),
        "data_room_package_files": (
            "UPDATE data_room_package_files SET source_binding_state='BOUND',"
            "source_lineage_object_id=CAST(:root_id AS uuid) "
            "WHERE file_entry_id=CAST(:native_id AS uuid)"
        ),
        "document_artifacts": (
            "UPDATE document_artifacts SET source_binding_state='BOUND',"
            "source_lineage_object_id=CAST(:root_id AS uuid),"
            "source_corpus_receipt_id=CAST(:package_id AS uuid) "
            "WHERE doc_id=CAST(:native_id AS uuid)"
        ),
        "documents": (
            "UPDATE documents SET source_binding_state='BOUND',"
            "source_lineage_object_id=CAST(:root_id AS uuid),"
            "source_corpus_receipt_id=CAST(:package_id AS uuid) "
            "WHERE document_id=CAST(:native_id AS uuid)"
        ),
        "document_spans": (
            "UPDATE document_spans SET source_binding_state='BOUND',"
            "source_lineage_object_id=CAST(:root_id AS uuid),"
            "source_corpus_receipt_id=CAST(:package_id AS uuid),"
            "parse_attempt_id=CAST(:parse_attempt_id AS uuid) "
            "WHERE span_id=CAST(:native_id AS uuid)"
        ),
    }
    for binding in plan["bindings"]:
        result = bind.execute(sa.text(update_sql[binding["table"]]), binding)
        if result.rowcount != 1:
            raise RuntimeError("TASK7_SOURCE_PACKAGE_MEMBERSHIP_UNRESOLVED")

    expected_roots = {
        row["source_lineage_object_id"]: (
            row["object_kind"],
            row["source_version"],
            row["source_disposition"],
            row["object_identity_sha256"],
            row["content_sha256"],
            row["corpus_receipt_sha256"],
            row["write_origin"],
        )
        for row in roots
    }
    actual_roots = {
        str(row[0]): tuple(row[1:])
        for row in bind.execute(
            sa.text(
                "SELECT source_lineage_object_id,object_kind,source_version,source_disposition,"
                "object_identity_sha256,content_sha256,corpus_receipt_sha256,write_origin "
                "FROM source_lineage_objects ORDER BY source_lineage_object_id"
            )
        )
    }
    if actual_roots != expected_roots:
        raise RuntimeError("TASK7_SOURCE_BACKFILL_RELOAD_MISMATCH")
    for table in (
        "data_room_packages",
        "data_room_package_files",
        "document_artifacts",
        "documents",
        "document_spans",
        "document_parse_attempts",
    ):
        if bool(
            bind.execute(
                sa.text(
                    f"SELECT EXISTS (SELECT 1 FROM {table} WHERE source_binding_state<>'BOUND')"
                )
            ).scalar_one()
        ):
            raise RuntimeError("TASK7_SOURCE_BACKFILL_RELOAD_MISMATCH")


class _LegacyStepClassification(NamedTuple):
    step_id: Any
    tenant_id: Any
    run_id: Any
    step_name: str
    step_order: int
    pipeline_contract_version: str
    pipeline_contract_hash: str
    semantic_rule_version: str
    semantic_rule_hash: str


class _LegacyRunClassification(NamedTuple):
    tenant_id: Any
    run_id: Any
    pipeline_contract_version: str
    pipeline_contract_hash: str
    pipeline_resume_state: str
    pipeline_resume_error_code: str | None
    canonical_step_count: int
    steps: tuple[_LegacyStepClassification, ...]


def _legacy_field(row: object, field: str) -> Any:
    if isinstance(row, Mapping):
        return row[field]
    mapping = getattr(row, "_mapping", None)
    if isinstance(mapping, Mapping):
        return mapping[field]
    return getattr(row, field)


def _legacy_classification_error(reason: str) -> RuntimeError:
    return RuntimeError(f"TASK7_LEGACY_RUN_CLASSIFICATION_INVALID:{reason}")


def _classify_legacy_run(
    *,
    run: Mapping[str, object] | object,
    steps: Sequence[Mapping[str, object] | object],
) -> _LegacyRunClassification:
    mode = str(_legacy_field(run, "mode"))
    status = str(_legacy_field(run, "status"))
    try:
        contract_version, registry = _LEGACY_MODE_REGISTRY[mode]
    except KeyError as exc:
        raise _legacy_classification_error("MODE") from exc
    if status == "RUNNING":
        raise _legacy_classification_error("RUNNING_RUN")
    if status not in {"QUEUED", "SUCCEEDED", "FAILED", "CANCELLED"}:
        raise _legacy_classification_error("RUN_STATUS")

    tenant_id = _legacy_field(run, "tenant_id")
    run_id = _legacy_field(run, "run_id")
    expected_by_name = {name: order for order, name in registry}
    expected_pairs = frozenset(registry)
    seen_names: set[str] = set()
    seen_orders: set[int] = set()
    canonical_pairs: set[tuple[int, str]] = set()
    classified_steps: list[_LegacyStepClassification] = []
    for step in steps:
        if str(_legacy_field(step, "tenant_id")) != str(tenant_id) or str(
            _legacy_field(step, "run_id")
        ) != str(run_id):
            raise _legacy_classification_error("CROSS_RUN_STEP")
        step_name = str(_legacy_field(step, "step_name"))
        step_order_value = _legacy_field(step, "step_order")
        if isinstance(step_order_value, bool) or not isinstance(step_order_value, int):
            raise _legacy_classification_error("STEP_ORDER_TYPE")
        step_order = step_order_value
        if step_order < 0:
            raise _legacy_classification_error("NEGATIVE_STEP_ORDER")
        if step_name in seen_names:
            raise _legacy_classification_error("DUPLICATE_STEP_NAME")
        if step_order in seen_orders:
            raise _legacy_classification_error("DUPLICATE_STEP_ORDER")
        seen_names.add(step_name)
        seen_orders.add(step_order)

        if step_name == "RUN_LIFECYCLE":
            if step_order != 28:
                raise _legacy_classification_error("AUXILIARY_STEP_ORDER")
        else:
            expected_order = expected_by_name.get(step_name)
            if expected_order is None:
                raise _legacy_classification_error("UNKNOWN_STEP_NAME")
            if step_order != expected_order:
                raise _legacy_classification_error("STEP_NAME_ORDER")
            canonical_pairs.add((step_order, step_name))

        step_status = str(_legacy_field(step, "status"))
        if step_status not in {"PENDING", "RUNNING", "COMPLETED", "FAILED", "BLOCKED"}:
            raise _legacy_classification_error("STEP_STATUS")
        if step_status == "RUNNING":
            raise _legacy_classification_error("RUNNING_STEP_WITHOUT_RUNNING_RUN")
        classified_steps.append(
            _LegacyStepClassification(
                step_id=_legacy_field(step, "step_id"),
                tenant_id=_legacy_field(step, "tenant_id"),
                run_id=_legacy_field(step, "run_id"),
                step_name=step_name,
                step_order=step_order,
                pipeline_contract_version=contract_version,
                pipeline_contract_hash=_LEGACY_CONTRACT_HASH,
                semantic_rule_version=_LEGACY_SEMANTIC_VERSION,
                semantic_rule_hash=_LEGACY_SEMANTIC_HASH,
            )
        )

    if not canonical_pairs <= expected_pairs:
        raise _legacy_classification_error("STEP_REGISTRY_SUBSET")
    exact_complete = canonical_pairs == expected_pairs
    if status == "SUCCEEDED":
        if not exact_complete:
            raise _legacy_classification_error("SUCCEEDED_INCOMPLETE")
        if any(
            str(_legacy_field(step, "status")) != "COMPLETED"
            for step in steps
            if str(_legacy_field(step, "step_name")) != "RUN_LIFECYCLE"
        ):
            raise _legacy_classification_error("SUCCEEDED_STEP_NOT_COMPLETED")
        resume_state = "HISTORICAL_TERMINAL"
        resume_error = None
    elif status in {"FAILED", "CANCELLED"} and exact_complete:
        resume_state = "HISTORICAL_TERMINAL"
        resume_error = None
    else:
        resume_state = "NON_RESUMABLE"
        resume_error = "LEGACY_RUN_RESUME_UNSAFE"

    return _LegacyRunClassification(
        tenant_id=tenant_id,
        run_id=run_id,
        pipeline_contract_version=contract_version,
        pipeline_contract_hash=_LEGACY_CONTRACT_HASH,
        pipeline_resume_state=resume_state,
        pipeline_resume_error_code=resume_error,
        canonical_step_count=len(canonical_pairs),
        steps=tuple(classified_steps),
    )


def _validate_legacy_runs_before_ddl() -> tuple[_LegacyRunClassification, ...]:
    bind = op.get_bind()
    runs = tuple(
        bind.execute(
            sa.text("SELECT tenant_id,run_id,mode,status FROM runs ORDER BY tenant_id,run_id")
        )
        .mappings()
        .all()
    )
    steps = tuple(
        bind.execute(
            sa.text(
                "SELECT tenant_id,run_id,step_id,step_name,step_order,status "
                "FROM run_steps ORDER BY tenant_id,run_id,step_order,step_id"
            )
        )
        .mappings()
        .all()
    )
    by_run: dict[str, list[Mapping[str, object]]] = {str(row["run_id"]): [] for row in runs}
    for step in steps:
        key = str(step["run_id"])
        if key not in by_run:
            raise _legacy_classification_error("ORPHAN_STEP")
        by_run[key].append(dict(step))
    return tuple(
        _classify_legacy_run(
            run=dict(run),
            steps=tuple(by_run[str(run["run_id"])]),
        )
        for run in runs
    )


def _lock_and_open_backfill_census() -> None:
    op.get_bind().execute(sa.text("SELECT pg_advisory_xact_lock(7380038)"))
    op.execute(
        "LOCK TABLE runs, run_steps, data_room_packages, data_room_package_files, "
        "document_artifacts, documents, document_spans IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "CREATE TEMP TABLE pg_temp.task7_backfill_census ("
        "contract_version TEXT NOT NULL, relation_name TEXT NOT NULL, row_count BIGINT NOT NULL, "
        "PRIMARY KEY (contract_version, relation_name)) ON COMMIT DROP"
    )
    op.execute(
        "INSERT INTO pg_temp.task7_backfill_census(contract_version,relation_name,row_count) "
        "VALUES ('task7-migration-backfill-census-v1','runs',(SELECT count(*) FROM runs)),"
        "('task7-migration-backfill-census-v1','run_steps',(SELECT count(*) FROM run_steps))"
    )


def _add_altered_columns_and_backfill(
    legacy_plan: Sequence[_LegacyRunClassification],
) -> None:
    for table in _ALTERED_TABLES:
        for definition in _DDL_ROWS[table]["column_tuple"]:
            initially_nullable = table in {"runs", "run_steps"} or (
                definition.startswith("source_binding_state ")
            )
            _add_column(table, definition, initially_nullable=initially_nullable)

    bind = op.get_bind()
    update_run = sa.text(
        "UPDATE runs SET pipeline_contract_version=:contract_version, "
        "pipeline_contract_hash=:contract_hash, "
        "pipeline_resume_state=:resume_state, "
        "pipeline_resume_error_code=:resume_error "
        "WHERE tenant_id=CAST(:tenant_id AS uuid) AND run_id=CAST(:run_id AS uuid)"
    )
    update_step = sa.text(
        "UPDATE run_steps SET pipeline_contract_version=:contract_version, "
        "pipeline_contract_hash=:contract_hash, "
        "semantic_rule_version=:semantic_version, "
        "semantic_rule_hash=:semantic_hash "
        "WHERE tenant_id=CAST(:tenant_id AS uuid) "
        "AND run_id=CAST(:run_id AS uuid) AND step_id=CAST(:step_id AS uuid)"
    )
    for classification in legacy_plan:
        run_result = bind.execute(
            update_run,
            {
                "contract_version": classification.pipeline_contract_version,
                "contract_hash": classification.pipeline_contract_hash,
                "resume_state": classification.pipeline_resume_state,
                "resume_error": classification.pipeline_resume_error_code,
                "tenant_id": str(classification.tenant_id),
                "run_id": str(classification.run_id),
            },
        )
        if run_result.rowcount != 1:
            raise RuntimeError("TASK7_LEGACY_RUN_BACKFILL_RELOAD_MISMATCH")
        for step in classification.steps:
            step_result = bind.execute(
                update_step,
                {
                    "contract_version": step.pipeline_contract_version,
                    "contract_hash": step.pipeline_contract_hash,
                    "semantic_version": step.semantic_rule_version,
                    "semantic_hash": step.semantic_rule_hash,
                    "tenant_id": str(step.tenant_id),
                    "run_id": str(step.run_id),
                    "step_id": str(step.step_id),
                },
            )
            if step_result.rowcount != 1:
                raise RuntimeError("TASK7_LEGACY_STEP_BACKFILL_RELOAD_MISMATCH")

    unclassified_runs = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM runs WHERE pipeline_contract_version IS NULL "
                "OR pipeline_contract_hash IS NULL OR pipeline_resume_state IS NULL"
            )
        ).scalar_one()
    )
    unclassified_steps = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM run_steps WHERE pipeline_contract_version IS NULL "
                "OR pipeline_contract_hash IS NULL OR semantic_rule_version IS NULL "
                "OR semantic_rule_hash IS NULL"
            )
        ).scalar_one()
    )
    if unclassified_runs or unclassified_steps:
        raise RuntimeError("TASK7_LEGACY_BACKFILL_RELOAD_MISMATCH")

    for table in ("runs", "run_steps"):
        for definition in _DDL_ROWS[table]["column_tuple"]:
            if " NOT NULL" in definition:
                column = definition.split(None, 1)[0]
                op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL")

    source_state_column = "source_binding_state"
    for table in (
        "data_room_packages",
        "data_room_package_files",
        "document_artifacts",
        "documents",
        "document_spans",
    ):
        op.execute(f"UPDATE {table} SET {source_state_column}='UNBOUND'")
        # Keep this loop as the single executable path; the six expanded review
        # literals above are the source-census authority checked by the gate.
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {source_state_column} SET NOT NULL")

    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM document_spans WHERE deal_id IS NULL) "
        "THEN RAISE EXCEPTION 'TASK7_SOURCE_BACKFILL_UNRECONSTRUCTIBLE' "
        "USING ERRCODE='23514'; END IF; END $$"
    )
    op.execute("ALTER TABLE document_spans ALTER COLUMN deal_id SET NOT NULL")


def _add_check_authorities(table: str) -> None:
    for authority in _DDL_ROWS[table]["check_tuple"]:
        name, expression, suffix = _split_named(authority)
        if suffix or _constraint_exists(table, name):
            if suffix:
                raise RuntimeError(f"TASK7_CHECK_AUTHORITY_INVALID:{authority}")
            continue
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression})")


def _add_unique_authorities(table: str) -> None:
    for authority in _DDL_ROWS[table]["unique_tuple"]:
        name, columns, suffix = _split_named(authority)
        if _constraint_exists(table, name) or _index_exists(name):
            continue
        if name.startswith("pk_"):
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} PRIMARY KEY ({columns})")
        elif name.startswith("ux_") or suffix.startswith("WHERE "):
            where = f" {suffix}" if suffix else ""
            op.execute(f"CREATE UNIQUE INDEX {name} ON {table} ({columns}){where}")
        elif name.startswith("uq_"):
            nulls = " NULLS NOT DISTINCT" if suffix == "NULLS NOT DISTINCT" else ""
            if suffix not in {"", "NULLS NOT DISTINCT"}:
                raise RuntimeError(f"TASK7_UNIQUE_AUTHORITY_INVALID:{authority}")
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE{nulls} ({columns})")
        else:
            raise RuntimeError(f"TASK7_UNIQUE_AUTHORITY_INVALID:{authority}")


def _add_index_authorities(table: str) -> None:
    for authority in _DDL_ROWS[table]["index_tuple"]:
        name, columns, suffix = _split_named(authority)
        if _index_exists(name):
            continue
        tail = f" {suffix}" if suffix else ""
        op.execute(f"CREATE INDEX {name} ON {table} ({columns}){tail}")


_FOREIGN_KEY_RE = re.compile(
    r"^(?P<name>[a-z0-9_]+)\((?P<child>[^)]*)\) -> "
    r"(?P<parent>[a-z0-9_]+)\.(?P<parent_key>[a-z0-9_]+)\((?P<parent_cols>[^)]*)\) "
    r"ON UPDATE (?P<update>NO ACTION|CASCADE|RESTRICT) "
    r"ON DELETE (?P<delete>NO ACTION|CASCADE|RESTRICT) "
    r"USING (?P<index>[a-z0-9_]+)(?P<deferred> DEFERRABLE INITIALLY DEFERRED)?$"
)


def _add_foreign_key_authorities(table: str) -> None:
    for authority in _DDL_ROWS[table]["foreign_key_tuple"]:
        match = _FOREIGN_KEY_RE.fullmatch(authority)
        if match is None:
            raise RuntimeError(f"TASK7_FOREIGN_KEY_AUTHORITY_INVALID:{authority}")
        values = match.groupdict()
        if _constraint_exists(table, values["name"]):
            continue
        deferred = values["deferred"] or ""
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {values['name']} "
            f"FOREIGN KEY ({values['child']}) REFERENCES {values['parent']} "
            f"({values['parent_cols']}) ON UPDATE {values['update']} "
            f"ON DELETE {values['delete']}{deferred}"
        )


def _replace_legacy_cascade_membership() -> None:
    op.execute(
        "ALTER TABLE data_room_package_files "
        "DROP CONSTRAINT data_room_package_files_package_id_fkey"
    )
    op.execute("ALTER TABLE document_spans DROP CONSTRAINT document_spans_document_id_fkey")


def _materialize_reviewed_authorities() -> None:
    for authority in BASELINE_PARENT_AUTHORITIES_0038_V1:
        (
            _ordinal,
            table,
            authority_name,
            authority_kind,
            _columns,
            predicate,
            installation_revision,
            upgrade_ddl,
            downgrade_disposition,
        ) = authority
        if (
            authority_kind != "UNIQUE_CONSTRAINT"
            or predicate is not None
            or installation_revision != "0038"
            or downgrade_disposition != "DROP_WITH_0038"
        ):
            raise RuntimeError(f"TASK7_BASELINE_PARENT_AUTHORITY_INVALID:{authority_name}")
        if not _constraint_exists(table, authority_name):
            op.execute(upgrade_ddl)
    for table in REVIEWED_CURRENT_DDL_TABLES_V1:
        _add_check_authorities(table)
        _add_unique_authorities(table)
        _add_index_authorities(table)
    for table in REVIEWED_CURRENT_DDL_TABLES_V1:
        _add_foreign_key_authorities(table)


def _enable_task7_rls() -> None:
    for table in TASK7_TABLES_V1:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "AS PERMISSIVE FOR ALL TO PUBLIC "
            f"USING ({CANONICAL_TENANT_PREDICATE}) "
            f"WITH CHECK ({CANONICAL_TENANT_PREDICATE})"
        )
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC")


def _assert_erasure_roles_ready() -> None:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname IN "
            "('idis_erasure_function_owner','idis_erasure_executor') ORDER BY rolname"
        )
    )
    posture = {str(row[0]): tuple(bool(value) for value in row[1:]) for row in rows}
    expected = {
        "idis_erasure_executor": (True, False, False, False, False, False),
        "idis_erasure_function_owner": (False, False, False, False, False, False),
    }
    if posture != expected:
        raise RuntimeError("TASK7_ERASURE_ROLE_POSTURE_INVALID")

    memberships = int(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM pg_catalog.pg_auth_members m JOIN "
                "pg_catalog.pg_roles member ON member.oid=m.member JOIN "
                "pg_catalog.pg_roles granted_role ON granted_role.oid=m.roleid "
                "WHERE member.rolname IN "
                "('idis_erasure_function_owner','idis_erasure_executor') "
                "OR granted_role.rolname IN "
                "('idis_erasure_function_owner','idis_erasure_executor')"
            )
        )
        .scalar_one()
    )
    if memberships != 0:
        raise RuntimeError("TASK7_ERASURE_ROLE_MEMBERSHIP_FORBIDDEN")


def _execute_authority_tuple(statements: tuple[str, ...]) -> None:
    for statement in statements:
        op.execute(statement)


def _run_v21_guard_authority_preflight() -> None:
    statements = TASK7_CURRENT_GUARD_ACL_INSTALL_SQL_V1
    if (
        len(statements) != 213
        or not all(statement.startswith("DO $$") for statement in statements[:3])
        or not statements[3].startswith("REVOKE ALL ON SCHEMA public")
    ):
        raise RuntimeError("TASK7_V21_GUARD_ACL_PREFLIGHT_ORDER_INVALID")
    _execute_authority_tuple(statements[:3])


def _install_current_erasure_authority() -> None:
    if len(_CURRENT_ERASURE_ACL_SQL_V1) != 209:
        raise RuntimeError("TASK7_V21_ERASURE_ACL_CENSUS_MISMATCH")
    _assert_erasure_roles_ready()
    _execute_authority_tuple(_CURRENT_ERASURE_BODY_SQL_V1)
    _execute_authority_tuple(_LEGACY_GUARD_UPGRADE_SQL_V1)
    _execute_authority_tuple(_HOLD_LOCK_UPGRADE_SQL_V1)
    _execute_authority_tuple(_CURRENT_ERASURE_ACL_SQL_V1)


def _install_v21_current_guard_authority() -> None:
    body = TASK7_CURRENT_GUARD_BODY_SQL_V1
    acl = TASK7_CURRENT_GUARD_ACL_INSTALL_SQL_V1
    if len(body) != 102 or len(acl) != 213:
        raise RuntimeError("TASK7_V21_CURRENT_GUARD_AUTHORITY_CENSUS_MISMATCH")

    # Erasure-aware retained helpers are installed first. The frozen v21 bodies
    # then replace the Task-5/6 guards before ownership and least-privilege ACLs.
    _install_current_erasure_authority()
    _execute_authority_tuple(body)
    _execute_authority_tuple(acl[3:])


def assert_downgrade_is_lossless() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE deliverable_manifest_references, run_consumer_receipts, "
            "run_expected_consumer_edges, run_applicability_decisions, run_step_outputs, "
            "run_input_bindings, run_output_objects, document_parse_attempts, "
            "source_lineage_objects, runs IN ACCESS EXCLUSIVE MODE"
        )
    )
    if bool(
        bind.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM runs WHERE "
                "pipeline_contract_version='full-strict-v21') "
                "OR EXISTS (SELECT 1 FROM run_output_objects) "
                "OR EXISTS (SELECT 1 FROM source_lineage_objects WHERE write_origin = 'RUNTIME') "
                "OR EXISTS (SELECT 1 FROM document_parse_attempts WHERE write_origin = 'RUNTIME') "
                "OR EXISTS (SELECT 1 FROM data_room_packages WHERE "
                "source_binding_state = 'UNBOUND') "
                "OR EXISTS (SELECT 1 FROM run_input_bindings) "
                "OR EXISTS (SELECT 1 FROM run_step_outputs) "
                "OR EXISTS (SELECT 1 FROM run_applicability_decisions) "
                "OR EXISTS (SELECT 1 FROM run_expected_consumer_edges) "
                "OR EXISTS (SELECT 1 FROM run_consumer_receipts) "
                "OR EXISTS (SELECT 1 FROM deliverable_manifest_references)"
            )
        ).scalar_one()
    ):
        raise RuntimeError("cannot downgrade Task 7 while full-strict-v21 runtime evidence exists")


def _current_erasure_bound_zeroing_sql_v1() -> tuple[str, ...]:
    zeroing_prefixes = (
        "REVOKE ALL PRIVILEGES ON SCHEMA ",
        "REVOKE ALL PRIVILEGES ON TABLE ",
        "REVOKE ALL PRIVILEGES ON SEQUENCE ",
    )
    zeroing = tuple(
        statement
        for statement in _CURRENT_ERASURE_ACL_SQL_V1
        if statement.startswith(zeroing_prefixes)
    )
    required_schema_zeroing = (
        "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_function_owner",
        "REVOKE ALL PRIVILEGES ON SCHEMA public FROM idis_erasure_executor",
    )
    if zeroing[:2] != required_schema_zeroing:
        raise RuntimeError("TASK7_ERASURE_SCHEMA_ZEROING_AUTHORITY_INVALID")

    bound_zeroing = set(zeroing)
    direct_grants = tuple(
        statement for statement in _CURRENT_ERASURE_ACL_SQL_V1 if statement.startswith("GRANT ")
    )
    expected_function_grant = (
        "GRANT EXECUTE ON FUNCTION "
        "public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) "
        "TO idis_erasure_executor"
    )
    for grant in direct_grants:
        if grant == expected_function_grant:
            continue
        if grant.startswith("GRANT USAGE ON SCHEMA "):
            relation_and_role = grant.removeprefix("GRANT USAGE ON SCHEMA ")
            relation, role = relation_and_role.rsplit(" TO ", 1)
            inverse = f"REVOKE ALL PRIVILEGES ON SCHEMA {relation} FROM {role}"
        elif " ON TABLE " in grant:
            relation_and_role = grant.split(" ON TABLE ", 1)[1]
            relation, role = relation_and_role.rsplit(" TO ", 1)
            inverse = f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM {role}"
        elif " ON SEQUENCE " in grant:
            relation_and_role = grant.split(" ON SEQUENCE ", 1)[1]
            relation, role = relation_and_role.rsplit(" TO ", 1)
            inverse = f"REVOKE ALL PRIVILEGES ON SEQUENCE {relation} FROM {role}"
        else:
            raise RuntimeError("TASK7_ERASURE_ACL_GRANT_SHAPE_UNSUPPORTED")
        if inverse not in bound_zeroing:
            raise RuntimeError("TASK7_ERASURE_ACL_ZEROING_CLOSURE_INVALID")
    return zeroing


def _remove_erasure_authority() -> None:
    _assert_erasure_roles_ready()
    _execute_authority_tuple(_HOLD_LOCK_DOWNGRADE_SQL_V1)
    _execute_authority_tuple(_LEGACY_GUARD_DOWNGRADE_SQL_V1)
    op.execute(
        "REVOKE ALL ON FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid) "
        "FROM idis_erasure_executor"
    )
    op.execute("DROP FUNCTION public.idis_task7_execute_approved_erasure(uuid,uuid,uuid)")
    for statement in _current_erasure_bound_zeroing_sql_v1():
        op.execute(statement)


def _remove_v21_guard_and_erasure_authority() -> None:
    statements = TASK7_CURRENT_GUARD_ACL_DOWNGRADE_SQL_V1
    restore_offset = 171
    if (
        len(statements) != 190
        or not statements[0].startswith("DROP TRIGGER ")
        or not statements[restore_offset].startswith("DO $$")
    ):
        raise RuntimeError("TASK7_V21_GUARD_ACL_DOWNGRADE_ORDER_INVALID")

    # Drop v21 triggers/functions and revoke guard-owner authority first, while
    # the erasure helper still exists. Remove the erasure authority next, then
    # restore the exact 0037 Task-5/6 function and trigger catalog bytes.
    _execute_authority_tuple(statements[:restore_offset])
    _remove_erasure_authority()
    _execute_authority_tuple(statements[restore_offset:])


def _drop_altered_foreign_keys_and_checks() -> None:
    for table in reversed(_ALTERED_TABLES):
        for authority in _DDL_ROWS[table]["foreign_key_tuple"]:
            name = authority.split("(", 1)[0]
            if _constraint_exists(table, name):
                op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    for table in reversed(_ALTERED_TABLES):
        for authority in reversed(_DDL_ROWS[table]["check_tuple"]):
            name = authority.split("(", 1)[0]
            if _constraint_exists(table, name):
                op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")


def _drop_altered_parent_authorities() -> None:
    # Task-7 child tables must be gone before their reviewed parent keys.
    for table in reversed(_ALTERED_TABLES):
        for authority in reversed(_DDL_ROWS[table]["unique_tuple"]):
            name = authority.split("(", 1)[0]
            if name in _PREEXISTING_INDEXES:
                continue
            if _constraint_exists(table, name):
                op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
            elif _index_exists(name):
                op.execute(f"DROP INDEX {name}")
        for authority in reversed(_DDL_ROWS[table]["index_tuple"]):
            name = authority.split("(", 1)[0]
            if name not in _PREEXISTING_INDEXES and _index_exists(name):
                op.execute(f"DROP INDEX {name}")


def _drop_altered_columns() -> None:
    for table in reversed(_ALTERED_TABLES):
        for definition in reversed(_DDL_ROWS[table]["column_tuple"]):
            column = definition.split(None, 1)[0]
            if table == "document_spans" and column == "deal_id":
                continue
            if _column_exists(table, column):
                op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    op.execute("ALTER TABLE document_spans ALTER COLUMN deal_id DROP NOT NULL")


def _restore_backfilled_source_bindings_for_downgrade() -> None:
    bind = op.get_bind()
    op.execute(
        "UPDATE data_room_packages p SET status='OPEN' "
        "FROM source_lineage_objects root "
        "WHERE root.source_lineage_object_id=p.source_lineage_object_id "
        "AND root.object_kind='PACKAGE' AND root.write_origin='BACKFILL' "
        "AND root.source_version LIKE 'legacy-open-freeze-v1:%'"
    )
    for table, key in (
        ("document_spans", "span_id"),
        ("data_room_package_files", "file_entry_id"),
        ("documents", "document_id"),
        ("document_artifacts", "doc_id"),
        ("data_room_packages", "package_id"),
    ):
        extra = ",parse_attempt_id=NULL" if table == "document_spans" else ""
        corpus = (
            ",source_corpus_receipt_id=NULL"
            if table in {"document_spans", "documents", "document_artifacts"}
            else ""
        )
        op.execute(
            f"UPDATE {table} SET source_binding_state='UNBOUND',"
            f"source_lineage_object_id=NULL{corpus}{extra} "
            "WHERE source_lineage_object_id IN (SELECT source_lineage_object_id "
            "FROM source_lineage_objects WHERE write_origin='BACKFILL')"
        )
        if bool(
            bind.execute(
                sa.text(
                    f"SELECT EXISTS (SELECT 1 FROM {table} WHERE "
                    f"source_lineage_object_id IS NOT NULL OR {key} IS NULL)"
                )
            ).scalar_one()
        ):
            raise RuntimeError("TASK7_DOWNGRADE_SOURCE_RESTORE_FAILED")


def upgrade() -> None:
    _assert_erasure_roles_ready()
    _run_v21_guard_authority_preflight()
    _lock_and_open_backfill_census()
    legacy_plan = _validate_legacy_runs_before_ddl()
    source_plan = _plan_source_backfill_before_mutation()
    _add_altered_columns_and_backfill(legacy_plan)
    _create_reviewed_tables()
    _apply_source_backfill(source_plan)
    _replace_legacy_cascade_membership()
    _materialize_reviewed_authorities()
    _enable_task7_rls()
    _install_v21_current_guard_authority()


def downgrade() -> None:
    assert_downgrade_is_lossless()
    _remove_v21_guard_and_erasure_authority()
    _drop_altered_foreign_keys_and_checks()
    _restore_backfilled_source_bindings_for_downgrade()
    for table in (
        "deliverable_manifest_references",
        "run_consumer_receipts",
        "run_expected_consumer_edges",
        "run_applicability_decisions",
        "run_step_outputs",
        "run_input_bindings",
        "document_parse_attempts",
        "run_output_objects",
        "source_lineage_objects",
    ):
        op.execute(f"DROP TABLE {table}")
    _drop_altered_parent_authorities()
    _drop_altered_columns()
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT uq_v2_evidence_item_generic_parent")
    op.execute("ALTER TABLE deals DROP CONSTRAINT uq_task7_deals_tenant_deal")
    op.execute(
        "ALTER TABLE data_room_package_files ADD CONSTRAINT "
        "data_room_package_files_package_id_fkey FOREIGN KEY (package_id) "
        "REFERENCES data_room_packages(package_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE document_spans ADD CONSTRAINT document_spans_document_id_fkey "
        "FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE"
    )
