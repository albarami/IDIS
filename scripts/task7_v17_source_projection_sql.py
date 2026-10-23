"""Hash-bound SQL builders for the full-strict-v17 source authority.

This module is deliberately hermetic: it accepts the frozen v16 authority
rows and returns the complete reviewed v17 statements.  It does not read the
environment, import runtime code, or inspect a database.
"""

from __future__ import annotations

import re
import textwrap
from typing import Any


class SourceProjectionSqlError(RuntimeError):
    """Stable failure raised when the frozen predecessor shape drifts."""


def _sql(value: str) -> str:
    """Render one deterministic, single-line SQL authority statement."""

    return re.sub(r"\s+", " ", textwrap.dedent(value).strip())


_SOURCE_CANONICAL_JSON = _sql(
    r"""
    CREATE FUNCTION public.idis_task7_source_canonical_jsonb_text_v1(p_value jsonb)
    RETURNS text
    LANGUAGE plpgsql
    IMMUTABLE
    SET search_path=pg_catalog,pg_temp
    AS $$
    DECLARE
      kind text;
      rendered text;
      numeric_text text;
      finite_value double precision;
    BEGIN
      kind:=pg_catalog.jsonb_typeof(p_value);
      IF kind='null' THEN
        RETURN 'null';
      ELSIF kind='boolean' THEN
        RETURN p_value::text;
      ELSIF kind='number' THEN
        numeric_text:=p_value::text;
        IF numeric_text~'^-?(0|[1-9][0-9]*)$' THEN
          RETURN numeric_text;
        END IF;
        finite_value:=numeric_text::double precision;
        IF finite_value::text IN ('NaN','Infinity','-Infinity') THEN
          RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
        END IF;
        IF finite_value=0::double precision THEN
          finite_value:=0::double precision;
        END IF;
        RETURN '{"float8_be":'
          ||pg_catalog.to_json(
              pg_catalog.encode(pg_catalog.float8send(finite_value),'hex')
            )::text
          ||'}';
      ELSIF kind='string' THEN
        RETURN pg_catalog.to_json(normalize(p_value#>>'{}',NFC))::text;
      ELSIF kind='array' THEN
        SELECT '['
          ||COALESCE(
              pg_catalog.string_agg(
                public.idis_task7_source_canonical_jsonb_text_v1(item),
                ',' ORDER BY ordinal
              ),
              ''
            )
          ||']'
        INTO rendered
        FROM pg_catalog.jsonb_array_elements(p_value)
          WITH ORDINALITY AS values(item,ordinal);
        RETURN rendered;
      ELSIF kind='object' THEN
        IF EXISTS (
          SELECT 1
          FROM pg_catalog.jsonb_object_keys(p_value) AS keys(key)
          GROUP BY normalize(key,NFC)
          HAVING count(*)<>1
        ) THEN
          RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
        END IF;
        SELECT '{'
          ||COALESCE(
              pg_catalog.string_agg(
                pg_catalog.to_json(normalize(key,NFC))::text
                  ||':'
                  ||public.idis_task7_source_canonical_jsonb_text_v1(value),
                ',' ORDER BY normalize(key,NFC) COLLATE "C"
              ),
              ''
            )
          ||'}'
        INTO rendered
        FROM pg_catalog.jsonb_each(p_value);
        RETURN rendered;
      END IF;
      RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
    EXCEPTION
      WHEN numeric_value_out_of_range OR invalid_text_representation THEN
        RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
    END
    $$
    """
)

_SOURCE_HASH_JSON = _sql(
    r"""
    CREATE FUNCTION public.idis_task7_source_hash_jsonb_v1(p_value jsonb)
    RETURNS text
    LANGUAGE sql
    IMMUTABLE
    SET search_path=pg_catalog,pg_temp
    AS $$
      SELECT pg_catalog.encode(
        pg_catalog.sha256(
          pg_catalog.convert_to(
            public.idis_task7_source_canonical_jsonb_text_v1(p_value),
            'UTF8'
          )
        ),
        'hex'
      )
    $$
    """
)

_SHA1 = _sql(
    r"""
    CREATE FUNCTION public.idis_task7_sha1_v1(p_value bytea)
    RETURNS bytea
    LANGUAGE plpgsql
    IMMUTABLE
    SET search_path=pg_catalog,pg_temp
    AS $$
    DECLARE
      payload bytea:=p_value;
      original_octets bigint:=pg_catalog.octet_length(p_value);
      zero_count integer;
      block_offset integer;
      word_ordinal integer;
      round_ordinal integer;
      words bigint[]:=pg_catalog.array_fill(0::bigint,ARRAY[80],ARRAY[0]);
      h0 bigint:=1732584193;
      h1 bigint:=4023233417;
      h2 bigint:=2562383102;
      h3 bigint:=271733878;
      h4 bigint:=3285377520;
      a bigint;
      b bigint;
      c bigint;
      d bigint;
      e bigint;
      f bigint;
      k bigint;
      temporary bigint;
      mask constant bigint:=4294967295;
      length_hex text;
      digest_hex text;
    BEGIN
      IF p_value IS NULL THEN
        RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
      END IF;
      payload:=payload||pg_catalog.decode('80','hex');
      zero_count:=(56-(pg_catalog.octet_length(payload)%64)+64)%64;
      IF zero_count>0 THEN
        payload:=payload||pg_catalog.decode(repeat('00',zero_count),'hex');
      END IF;
      length_hex:=pg_catalog.lpad(pg_catalog.to_hex(original_octets*8),16,'0');
      payload:=payload||pg_catalog.decode(length_hex,'hex');
      block_offset:=0;
      WHILE block_offset<pg_catalog.octet_length(payload) LOOP
        FOR word_ordinal IN 0..15 LOOP
          words[word_ordinal]:=(
              (pg_catalog.get_byte(payload,block_offset+word_ordinal*4)::bigint<<24)
            | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+1)::bigint<<16)
            | (pg_catalog.get_byte(payload,block_offset+word_ordinal*4+2)::bigint<<8)
            | pg_catalog.get_byte(payload,block_offset+word_ordinal*4+3)::bigint
          )&mask;
        END LOOP;
        FOR word_ordinal IN 16..79 LOOP
          temporary:=(
            words[word_ordinal-3]
            # words[word_ordinal-8]
            # words[word_ordinal-14]
            # words[word_ordinal-16]
          )&mask;
          words[word_ordinal]:=((temporary<<1)|(temporary>>31))&mask;
        END LOOP;
        a:=h0;
        b:=h1;
        c:=h2;
        d:=h3;
        e:=h4;
        FOR round_ordinal IN 0..79 LOOP
          IF round_ordinal<=19 THEN
            f:=((b&c)|((mask#b)&d))&mask;
            k:=1518500249;
          ELSIF round_ordinal<=39 THEN
            f:=(b#c#d)&mask;
            k:=1859775393;
          ELSIF round_ordinal<=59 THEN
            f:=((b&c)|(b&d)|(c&d))&mask;
            k:=2400959708;
          ELSE
            f:=(b#c#d)&mask;
            k:=3395469782;
          END IF;
          temporary:=(
            (((a<<5)|(a>>27))&mask)+f+e+k+words[round_ordinal]
          )&mask;
          e:=d;
          d:=c;
          c:=((b<<30)|(b>>2))&mask;
          b:=a;
          a:=temporary;
        END LOOP;
        h0:=(h0+a)&mask;
        h1:=(h1+b)&mask;
        h2:=(h2+c)&mask;
        h3:=(h3+d)&mask;
        h4:=(h4+e)&mask;
        block_offset:=block_offset+64;
      END LOOP;
      digest_hex:=
          pg_catalog.lpad(pg_catalog.to_hex(h0),8,'0')
        ||pg_catalog.lpad(pg_catalog.to_hex(h1),8,'0')
        ||pg_catalog.lpad(pg_catalog.to_hex(h2),8,'0')
        ||pg_catalog.lpad(pg_catalog.to_hex(h3),8,'0')
        ||pg_catalog.lpad(pg_catalog.to_hex(h4),8,'0');
      RETURN pg_catalog.decode(digest_hex,'hex');
    END
    $$
    """
)

_UUID5 = _sql(
    r"""
    CREATE FUNCTION public.idis_task7_uuidv5_v1(p_namespace uuid,p_name text)
    RETURNS uuid
    LANGUAGE plpgsql
    IMMUTABLE
    SET search_path=pg_catalog,pg_temp
    AS $$
    DECLARE
      namespace_bytes bytea;
      digest bytea;
      value_hex text;
    BEGIN
      IF p_namespace IS NULL OR p_name IS NULL THEN
        RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
      END IF;
      namespace_bytes:=pg_catalog.decode(
        replace(p_namespace::text,'-',''),
        'hex'
      );
      digest:=pg_catalog.substring(
        public.idis_task7_sha1_v1(
          namespace_bytes||pg_catalog.convert_to(p_name,'UTF8')
        ),
        1,
        16
      );
      digest:=pg_catalog.set_byte(
        digest,
        6,
        (pg_catalog.get_byte(digest,6)&15)|80
      );
      digest:=pg_catalog.set_byte(
        digest,
        8,
        (pg_catalog.get_byte(digest,8)&63)|128
      );
      value_hex:=pg_catalog.encode(digest,'hex');
      RETURN (
          pg_catalog.substring(value_hex,1,8)
        ||'-'||pg_catalog.substring(value_hex,9,4)
        ||'-'||pg_catalog.substring(value_hex,13,4)
        ||'-'||pg_catalog.substring(value_hex,17,4)
        ||'-'||pg_catalog.substring(value_hex,21,12)
      )::uuid;
    END
    $$
    """
)

_SOURCE_PROJECTION = _sql(
    r"""
    CREATE FUNCTION public.idis_task7_source_projection_v1(p_root_id uuid)
    RETURNS jsonb
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp
    AS $$
    DECLARE
      root_row record;
      package_row record;
      file_row record;
      artifact_row record;
      document_row record;
      attempt_row record;
      span_row record;
      manifest jsonb;
      manifest_file jsonb;
      manifest_members jsonb;
      metadata jsonb;
      schema_refs jsonb;
      terminal_projection jsonb;
      integrity jsonb;
      native_scope jsonb;
      expected_version text;
      expected_disposition text;
      expected_identity text;
      expected_content text;
      expected_corpus text;
      terminal_receipt text;
      locator_sha text;
      artifact_metadata_sha text;
      expected_root_id uuid;
      name_hash text;
      support_status text;
      triage_status text;
      storage_uri_sha text;
      matching_count bigint;
    BEGIN
      SELECT
        root.source_lineage_object_id,
        root.tenant_id,
        root.deal_id,
        root.corpus_receipt_id,
        root.object_kind,
        root.source_version,
        root.source_disposition,
        root.object_identity_sha256,
        root.content_sha256,
        root.corpus_receipt_sha256,
        root.write_origin
      INTO STRICT root_row
      FROM public.source_lineage_objects AS root
      WHERE root.source_lineage_object_id=p_root_id;
      IF root_row.write_origin<>'RUNTIME' THEN
        RETURN pg_catalog.jsonb_build_object(
          'source_lineage_object_id',root_row.source_lineage_object_id::text,
          'tenant_id',root_row.tenant_id::text,
          'deal_id',root_row.deal_id::text,
          'corpus_receipt_id',root_row.corpus_receipt_id::text,
          'object_kind',root_row.object_kind,
          'source_version',root_row.source_version,
          'source_disposition',root_row.source_disposition,
          'object_identity_sha256',root_row.object_identity_sha256,
          'content_sha256',root_row.content_sha256,
          'corpus_receipt_sha256',root_row.corpus_receipt_sha256,
          'write_origin',root_row.write_origin
        );
      END IF;

      IF root_row.object_kind='PACKAGE' THEN
        SELECT
          p.package_id,p.tenant_id,p.deal_id,p.status,p.metadata,
          p.source_binding_state,p.source_lineage_object_id
        INTO STRICT package_row
        FROM public.data_room_packages AS p
        WHERE p.source_lineage_object_id=p_root_id
          AND p.tenant_id=root_row.tenant_id
          AND p.deal_id=root_row.deal_id
          AND p.source_binding_state='BOUND';
        manifest:=package_row.metadata->'source_manifest';
        IF pg_catalog.jsonb_typeof(manifest)<>'object'
          OR pg_catalog.jsonb_typeof(manifest->'files')<>'array'
        THEN
          RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
        END IF;
        SELECT pg_catalog.jsonb_agg(
          pg_catalog.jsonb_build_array(
            (item->>'sequence')::integer,
            item->>'source_locator_sha256',
            item->>'content_sha256',
            (item->>'size_bytes')::bigint,
            item->>'extension',
            (item->>'document_id')::uuid::text,
            (item->>'doc_id')::uuid::text
          )
          ORDER BY ordinal
        )
        INTO manifest_members
        FROM pg_catalog.jsonb_array_elements(manifest->'files')
          WITH ORDINALITY AS members(item,ordinal);
        expected_version:='source-package-manifest-v1:'||(manifest->>'manifest_sha256');
        expected_disposition:='CORPUS';
        expected_identity:=public.idis_task7_source_hash_jsonb_v1(
          pg_catalog.jsonb_build_array(
            'source-package-identity-v1',
            package_row.package_id::text,
            manifest->>'schema_version',
            manifest->>'manifest_sha256',
            manifest->>'source_tree_fingerprint',
            (manifest->>'file_count')::integer,
            (manifest->>'total_bytes')::bigint,
            manifest_members
          )
        );
        expected_content:=manifest->>'manifest_sha256';
        expected_corpus:=manifest->>'manifest_sha256';
        native_scope:=pg_catalog.jsonb_build_array(
          root_row.tenant_id::text,
          root_row.deal_id::text,
          package_row.package_id::text
        );
      ELSE
        IF root_row.object_kind='PACKAGE_FILE' THEN
          SELECT
            f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence,
            f.path_hash,f.extension,f.sha256,f.file_status,f.support_status,
            f.triage_status,f.parse_status,f.reason_codes,f.error_codes,
            f.doc_id,f.document_id,f.storage_uri,f.source_binding_state,
            f.source_lineage_object_id,f.source_corpus_receipt_id
          INTO STRICT file_row
          FROM public.data_room_package_files AS f
          WHERE f.source_lineage_object_id=p_root_id
            AND f.tenant_id=root_row.tenant_id
            AND f.deal_id=root_row.deal_id
            AND f.source_binding_state='BOUND';
        ELSIF root_row.object_kind='ARTIFACT' THEN
          SELECT
            a.doc_id,a.tenant_id,a.deal_id,a.doc_type,a.title,
            a.source_system,a.version_id,a.ingested_at,a.sha256,a.uri,
            a.metadata,a.source_binding_state,a.source_lineage_object_id,
            a.source_corpus_receipt_id
          INTO STRICT artifact_row
          FROM public.document_artifacts AS a
          WHERE a.source_lineage_object_id=p_root_id
            AND a.tenant_id=root_row.tenant_id
            AND a.deal_id=root_row.deal_id
            AND a.source_binding_state='BOUND';
          SELECT
            f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence,
            f.path_hash,f.extension,f.sha256,f.file_status,f.support_status,
            f.triage_status,f.parse_status,f.reason_codes,f.error_codes,
            f.doc_id,f.document_id,f.storage_uri,f.source_binding_state,
            f.source_lineage_object_id,f.source_corpus_receipt_id
          INTO STRICT file_row
          FROM public.data_room_package_files AS f
          WHERE f.tenant_id=root_row.tenant_id
            AND f.deal_id=root_row.deal_id
            AND f.package_id=root_row.corpus_receipt_id
            AND f.doc_id=artifact_row.doc_id
            AND f.source_binding_state='BOUND';
        ELSIF root_row.object_kind='DOCUMENT' THEN
          SELECT
            d.document_id,d.tenant_id,d.deal_id,d.doc_id,d.doc_type,
            d.parse_status,d.metadata,d.source_binding_state,
            d.source_lineage_object_id,d.source_corpus_receipt_id
          INTO STRICT document_row
          FROM public.documents AS d
          WHERE d.source_lineage_object_id=p_root_id
            AND d.tenant_id=root_row.tenant_id
            AND d.deal_id=root_row.deal_id
            AND d.source_binding_state='BOUND';
          SELECT
            f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence,
            f.path_hash,f.extension,f.sha256,f.file_status,f.support_status,
            f.triage_status,f.parse_status,f.reason_codes,f.error_codes,
            f.doc_id,f.document_id,f.storage_uri,f.source_binding_state,
            f.source_lineage_object_id,f.source_corpus_receipt_id
          INTO STRICT file_row
          FROM public.data_room_package_files AS f
          WHERE f.tenant_id=root_row.tenant_id
            AND f.deal_id=root_row.deal_id
            AND f.package_id=root_row.corpus_receipt_id
            AND f.document_id=document_row.document_id
            AND f.source_binding_state='BOUND';
        ELSIF root_row.object_kind='PARSE_ATTEMPT' THEN
          SELECT
            a.parse_attempt_id,a.tenant_id,a.deal_id,a.document_id,
            a.attempt_ordinal,a.parser_name,a.parser_version,a.input_sha256,
            a.receipt_sha256,a.status,a.started_at,a.finished_at,
            a.source_binding_state,a.source_lineage_object_id,
            a.source_corpus_receipt_id,a.write_origin
          INTO STRICT attempt_row
          FROM public.document_parse_attempts AS a
          WHERE a.source_lineage_object_id=p_root_id
            AND a.tenant_id=root_row.tenant_id
            AND a.deal_id=root_row.deal_id
            AND a.source_binding_state='BOUND';
          SELECT
            d.document_id,d.tenant_id,d.deal_id,d.doc_id,d.doc_type,
            d.parse_status,d.metadata,d.source_binding_state,
            d.source_lineage_object_id,d.source_corpus_receipt_id
          INTO STRICT document_row
          FROM public.documents AS d
          WHERE d.tenant_id=root_row.tenant_id
            AND d.deal_id=root_row.deal_id
            AND d.document_id=attempt_row.document_id
            AND d.source_corpus_receipt_id=root_row.corpus_receipt_id
            AND d.source_binding_state='BOUND';
          SELECT
            f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence,
            f.path_hash,f.extension,f.sha256,f.file_status,f.support_status,
            f.triage_status,f.parse_status,f.reason_codes,f.error_codes,
            f.doc_id,f.document_id,f.storage_uri,f.source_binding_state,
            f.source_lineage_object_id,f.source_corpus_receipt_id
          INTO STRICT file_row
          FROM public.data_room_package_files AS f
          WHERE f.tenant_id=root_row.tenant_id
            AND f.deal_id=root_row.deal_id
            AND f.package_id=root_row.corpus_receipt_id
            AND f.document_id=document_row.document_id
            AND f.source_binding_state='BOUND';
        ELSIF root_row.object_kind='SPAN' THEN
          SELECT
            s.span_id,s.tenant_id,s.deal_id,s.document_id,s.span_type,
            s.locator,s.content_hash,s.parse_attempt_id,
            s.source_binding_state,s.source_lineage_object_id,
            s.source_corpus_receipt_id
          INTO STRICT span_row
          FROM public.document_spans AS s
          WHERE s.source_lineage_object_id=p_root_id
            AND s.tenant_id=root_row.tenant_id
            AND s.deal_id=root_row.deal_id
            AND s.source_binding_state='BOUND';
          SELECT
            a.parse_attempt_id,a.tenant_id,a.deal_id,a.document_id,
            a.attempt_ordinal,a.parser_name,a.parser_version,a.input_sha256,
            a.receipt_sha256,a.status,a.started_at,a.finished_at,
            a.source_binding_state,a.source_lineage_object_id,
            a.source_corpus_receipt_id,a.write_origin
          INTO STRICT attempt_row
          FROM public.document_parse_attempts AS a
          WHERE a.tenant_id=root_row.tenant_id
            AND a.deal_id=root_row.deal_id
            AND a.parse_attempt_id=span_row.parse_attempt_id
            AND a.source_corpus_receipt_id=root_row.corpus_receipt_id
            AND a.source_binding_state='BOUND';
          SELECT
            d.document_id,d.tenant_id,d.deal_id,d.doc_id,d.doc_type,
            d.parse_status,d.metadata,d.source_binding_state,
            d.source_lineage_object_id,d.source_corpus_receipt_id
          INTO STRICT document_row
          FROM public.documents AS d
          WHERE d.tenant_id=root_row.tenant_id
            AND d.deal_id=root_row.deal_id
            AND d.document_id=span_row.document_id
            AND d.source_corpus_receipt_id=root_row.corpus_receipt_id
            AND d.source_binding_state='BOUND';
          SELECT
            f.file_entry_id,f.tenant_id,f.package_id,f.deal_id,f.sequence,
            f.path_hash,f.extension,f.sha256,f.file_status,f.support_status,
            f.triage_status,f.parse_status,f.reason_codes,f.error_codes,
            f.doc_id,f.document_id,f.storage_uri,f.source_binding_state,
            f.source_lineage_object_id,f.source_corpus_receipt_id
          INTO STRICT file_row
          FROM public.data_room_package_files AS f
          WHERE f.tenant_id=root_row.tenant_id
            AND f.deal_id=root_row.deal_id
            AND f.package_id=root_row.corpus_receipt_id
            AND f.document_id=document_row.document_id
            AND f.source_binding_state='BOUND';
        ELSE
          RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
        END IF;

        SELECT
          p.package_id,p.tenant_id,p.deal_id,p.status,p.metadata,
          p.source_binding_state,p.source_lineage_object_id
        INTO STRICT package_row
        FROM public.data_room_packages AS p
        WHERE p.tenant_id=root_row.tenant_id
          AND p.deal_id=root_row.deal_id
          AND p.package_id=root_row.corpus_receipt_id
          AND p.source_binding_state='BOUND';
        manifest:=package_row.metadata->'source_manifest';
        IF pg_catalog.jsonb_typeof(manifest)<>'object'
          OR pg_catalog.jsonb_typeof(manifest->'files')<>'array'
        THEN
          RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
        END IF;
        SELECT item INTO STRICT manifest_file
        FROM pg_catalog.jsonb_array_elements(manifest->'files') AS files(item)
        WHERE (item->>'sequence')::integer=file_row.sequence;
        IF manifest_file->>'source_locator_sha256' IS DISTINCT FROM file_row.path_hash
          OR manifest_file->>'content_sha256' IS DISTINCT FROM file_row.sha256
          OR manifest_file->>'extension' IS DISTINCT FROM file_row.extension
          OR (manifest_file->>'document_id')::uuid IS DISTINCT FROM file_row.document_id
          OR (manifest_file->>'doc_id')::uuid IS DISTINCT FROM file_row.doc_id
        THEN
          RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
        END IF;

        IF document_row IS NULL THEN
          SELECT
            d.document_id,d.tenant_id,d.deal_id,d.doc_id,d.doc_type,
            d.parse_status,d.metadata,d.source_binding_state,
            d.source_lineage_object_id,d.source_corpus_receipt_id
          INTO STRICT document_row
          FROM public.documents AS d
          WHERE d.tenant_id=root_row.tenant_id
            AND d.deal_id=root_row.deal_id
            AND d.document_id=file_row.document_id
            AND d.source_corpus_receipt_id=root_row.corpus_receipt_id
            AND d.source_binding_state='BOUND';
        END IF;
        IF attempt_row IS NULL THEN
          SELECT count(*) INTO matching_count
          FROM public.document_parse_attempts AS a
          WHERE a.tenant_id=root_row.tenant_id
            AND a.deal_id=root_row.deal_id
            AND a.document_id=document_row.document_id
            AND a.source_corpus_receipt_id=root_row.corpus_receipt_id
            AND a.source_binding_state='BOUND';
          IF matching_count<>1 THEN
            RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
          END IF;
          SELECT
            a.parse_attempt_id,a.tenant_id,a.deal_id,a.document_id,
            a.attempt_ordinal,a.parser_name,a.parser_version,a.input_sha256,
            a.receipt_sha256,a.status,a.started_at,a.finished_at,
            a.source_binding_state,a.source_lineage_object_id,
            a.source_corpus_receipt_id,a.write_origin
          INTO STRICT attempt_row
          FROM public.document_parse_attempts AS a
          WHERE a.tenant_id=root_row.tenant_id
            AND a.deal_id=root_row.deal_id
            AND a.document_id=document_row.document_id
            AND a.source_corpus_receipt_id=root_row.corpus_receipt_id
            AND a.source_binding_state='BOUND';
        END IF;

        metadata:=document_row.metadata;
        support_status:=CASE
          WHEN metadata->>'parser_support_status' IN (
            'supported','partially_supported','unsupported','encrypted',
            'scanned_or_image_only','too_large','corrupted',
            'conversion_required','excluded_confirmed','unknown'
          ) THEN metadata->>'parser_support_status'
          ELSE 'unknown'
        END;
        triage_status:=CASE
          WHEN metadata->>'parser_triage_status' IN (
            'ready','partial','blocked','conversion_required',
            'unsupported_source','ocr_required','too_large',
            'excluded_confirmed','unknown'
          ) THEN metadata->>'parser_triage_status'
          ELSE 'unknown'
        END;
        schema_refs:=COALESCE(metadata->'applicable_schema_refs','[]'::jsonb);
        IF pg_catalog.jsonb_typeof(schema_refs)<>'array' THEN
          RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
        END IF;
        terminal_projection:=pg_catalog.jsonb_build_object(
          'parser_support_status',support_status,
          'parser_triage_status',triage_status,
          'applicable_schema_refs',schema_refs
        );
        terminal_receipt:=public.idis_task7_source_hash_jsonb_v1(
          pg_catalog.jsonb_build_array(
            'terminal-document-metadata-v2',
            document_row.doc_type,
            document_row.parse_status,
            terminal_projection
          )
        );
        expected_disposition:=CASE document_row.parse_status
          WHEN 'PARSED' THEN 'EVIDENCE_BEARING'
          WHEN 'FAILED' THEN 'CONFIRMED_EXCLUSION'
          ELSE NULL
        END;
        IF expected_disposition IS NULL
          OR attempt_row.receipt_sha256 IS DISTINCT FROM terminal_receipt
          OR attempt_row.input_sha256 IS DISTINCT FROM manifest_file->>'content_sha256'
          OR attempt_row.status IS DISTINCT FROM document_row.parse_status
          OR attempt_row.write_origin<>'RUNTIME'
        THEN
          RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
        END IF;

        IF root_row.object_kind='PACKAGE_FILE' THEN
          storage_uri_sha:=CASE
            WHEN file_row.storage_uri IS NULL THEN NULL
            ELSE pg_catalog.encode(
              pg_catalog.sha256(
                pg_catalog.convert_to(file_row.storage_uri,'UTF8')
              ),
              'hex'
            )
          END;
          expected_version:=
            'source-package-file-v1:'
            ||(manifest->>'manifest_sha256')
            ||':'
            ||file_row.sequence::text;
          expected_identity:=public.idis_task7_source_hash_jsonb_v1(
            pg_catalog.jsonb_build_array(
              'source-package-file-identity-v1',
              package_row.package_id::text,
              file_row.file_entry_id::text,
              file_row.sequence,
              file_row.path_hash,
              file_row.extension,
              file_row.sha256,
              (manifest_file->>'size_bytes')::bigint,
              file_row.document_id::text,
              file_row.doc_id::text,
              file_row.file_status,
              file_row.support_status,
              file_row.triage_status,
              file_row.parse_status,
              file_row.reason_codes,
              file_row.error_codes,
              storage_uri_sha,
              expected_disposition,
              terminal_receipt
            )
          );
          native_scope:=pg_catalog.jsonb_build_array(
            root_row.tenant_id::text,
            root_row.deal_id::text,
            package_row.package_id::text,
            file_row.file_entry_id::text
          );
        ELSIF root_row.object_kind='ARTIFACT' THEN
          IF artifact_row IS NULL THEN
            SELECT
              a.doc_id,a.tenant_id,a.deal_id,a.doc_type,a.title,
              a.source_system,a.version_id,a.ingested_at,a.sha256,a.uri,
              a.metadata,a.source_binding_state,a.source_lineage_object_id,
              a.source_corpus_receipt_id
            INTO STRICT artifact_row
            FROM public.document_artifacts AS a
            WHERE a.tenant_id=root_row.tenant_id
              AND a.deal_id=root_row.deal_id
              AND a.doc_id=file_row.doc_id
              AND a.source_corpus_receipt_id=root_row.corpus_receipt_id
              AND a.source_binding_state='BOUND';
          END IF;
          integrity:=artifact_row.metadata->'durable_object_integrity';
          IF pg_catalog.jsonb_typeof(integrity)<>'object'
            OR (
              SELECT count(*) FROM pg_catalog.jsonb_object_keys(integrity)
            )<>8
            OR NOT integrity ?& ARRAY[
              'schema_version','doc_id','content_sha256','size_bytes',
              'uri_sha256','version_id','backend','streaming_verified'
            ]
          THEN
            RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
          END IF;
          integrity:=pg_catalog.jsonb_build_object(
            'schema_version',integrity->>'schema_version',
            'doc_id',(integrity->>'doc_id')::uuid::text,
            'content_sha256',integrity->>'content_sha256',
            'size_bytes',(integrity->>'size_bytes')::bigint,
            'uri_sha256',integrity->>'uri_sha256',
            'version_id',(integrity->>'version_id')::uuid::text,
            'backend',integrity->>'backend',
            'streaming_verified',(integrity->>'streaming_verified')::boolean
          );
          IF artifact_row.uri IS NULL
            OR artifact_row.sha256 IS DISTINCT FROM manifest_file->>'content_sha256'
            OR integrity->>'schema_version'<>'durable-object-integrity-v1'
            OR (integrity->>'doc_id')::uuid IS DISTINCT FROM artifact_row.doc_id
            OR integrity->>'content_sha256' IS DISTINCT FROM artifact_row.sha256
            OR (integrity->>'size_bytes')::bigint
              IS DISTINCT FROM (manifest_file->>'size_bytes')::bigint
            OR integrity->>'uri_sha256' IS DISTINCT FROM pg_catalog.encode(
              pg_catalog.sha256(pg_catalog.convert_to(artifact_row.uri,'UTF8')),
              'hex'
            )
            OR (integrity->>'version_id')::uuid
              IS DISTINCT FROM artifact_row.version_id::uuid
            OR integrity->>'backend' NOT LIKE 'compliant:%'
            OR (integrity->>'streaming_verified')::boolean IS NOT TRUE
          THEN
            RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
          END IF;
          artifact_metadata_sha:=public.idis_task7_source_hash_jsonb_v1(
            pg_catalog.jsonb_build_array(
              'document-artifact-metadata-v2',
              pg_catalog.encode(
                pg_catalog.sha256(
                  pg_catalog.convert_to(artifact_row.title,'UTF8')
                ),
                'hex'
              ),
              artifact_row.source_system,
              artifact_row.doc_type,
              artifact_row.version_id::uuid::text,
              integrity
            )
          );
          expected_version:=
            'document-artifact-v1:'||artifact_row.version_id::uuid::text;
          expected_identity:=public.idis_task7_source_hash_jsonb_v1(
            pg_catalog.jsonb_build_array(
              'document-artifact-identity-v1',
              artifact_row.doc_id::text,
              artifact_row.doc_type,
              artifact_row.source_system,
              artifact_row.version_id::uuid::text,
              pg_catalog.to_char(
                artifact_row.ingested_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
              ),
              manifest_file->>'content_sha256',
              pg_catalog.encode(
                pg_catalog.sha256(
                  pg_catalog.convert_to(artifact_row.uri,'UTF8')
                ),
                'hex'
              ),
              artifact_metadata_sha
            )
          );
          native_scope:=pg_catalog.jsonb_build_array(
            root_row.tenant_id::text,
            root_row.deal_id::text,
            artifact_row.doc_id::text
          );
        ELSIF root_row.object_kind='DOCUMENT' THEN
          expected_version:='parsed-document-v1:'||terminal_receipt;
          expected_identity:=public.idis_task7_source_hash_jsonb_v1(
            pg_catalog.jsonb_build_array(
              'parsed-document-identity-v1',
              document_row.document_id::text,
              document_row.doc_id::text,
              document_row.doc_type,
              document_row.parse_status,
              terminal_receipt,
              attempt_row.parse_attempt_id::text,
              attempt_row.receipt_sha256
            )
          );
          native_scope:=pg_catalog.jsonb_build_array(
            root_row.tenant_id::text,
            root_row.deal_id::text,
            document_row.document_id::text
          );
        ELSIF root_row.object_kind='PARSE_ATTEMPT' THEN
          expected_version:=
            'parser-attempt-v1:'
            ||attempt_row.parser_name
            ||':'
            ||attempt_row.parser_version;
          expected_identity:=public.idis_task7_source_hash_jsonb_v1(
            pg_catalog.jsonb_build_array(
              'parse-attempt-identity-v1',
              attempt_row.parse_attempt_id::text,
              attempt_row.document_id::text,
              attempt_row.attempt_ordinal,
              attempt_row.parser_name,
              attempt_row.parser_version,
              attempt_row.input_sha256,
              attempt_row.receipt_sha256,
              attempt_row.status,
              pg_catalog.to_char(
                attempt_row.started_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
              ),
              pg_catalog.to_char(
                attempt_row.finished_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
              )
            )
          );
          native_scope:=pg_catalog.jsonb_build_array(
            root_row.tenant_id::text,
            root_row.deal_id::text,
            attempt_row.document_id::text,
            attempt_row.parse_attempt_id::text
          );
        ELSE
          locator_sha:=public.idis_task7_source_hash_jsonb_v1(
            pg_catalog.jsonb_build_array(
              'span-locator-v1',
              span_row.span_type,
              span_row.locator
            )
          );
          expected_version:=
            'document-span-v1:'
            ||attempt_row.parse_attempt_id::text
            ||':'
            ||span_row.span_type;
          expected_disposition:='EVIDENCE_BEARING';
          expected_identity:=public.idis_task7_source_hash_jsonb_v1(
            pg_catalog.jsonb_build_array(
              'document-span-identity-v1',
              span_row.span_id::text,
              span_row.document_id::text,
              attempt_row.parse_attempt_id::text,
              span_row.span_type,
              locator_sha,
              span_row.content_hash
            )
          );
          expected_content:=span_row.content_hash;
          native_scope:=pg_catalog.jsonb_build_array(
            root_row.tenant_id::text,
            root_row.deal_id::text,
            span_row.document_id::text,
            span_row.span_id::text
          );
        END IF;
        IF root_row.object_kind<>'SPAN' THEN
          expected_content:=manifest_file->>'content_sha256';
        END IF;
        expected_corpus:=manifest->>'manifest_sha256';
      END IF;

      name_hash:=public.idis_task7_source_hash_jsonb_v1(
        pg_catalog.jsonb_build_array(
          'source-lineage-object-v2',
          root_row.tenant_id::text,
          root_row.deal_id::text,
          root_row.corpus_receipt_id::text,
          root_row.object_kind,
          native_scope,
          expected_version,
          expected_disposition,
          expected_identity,
          expected_content,
          expected_corpus
        )
      );
      expected_root_id:=public.idis_task7_uuidv5_v1(
        '90a5de51-2753-5547-9375-ac5312629947'::uuid,
        name_hash
      );
      RETURN pg_catalog.jsonb_build_object(
        'source_lineage_object_id',expected_root_id::text,
        'tenant_id',root_row.tenant_id::text,
        'deal_id',root_row.deal_id::text,
        'corpus_receipt_id',root_row.corpus_receipt_id::text,
        'object_kind',root_row.object_kind,
        'source_version',expected_version,
        'source_disposition',expected_disposition,
        'object_identity_sha256',expected_identity,
        'content_sha256',expected_content,
        'corpus_receipt_sha256',expected_corpus,
        'write_origin','RUNTIME'
      );
    EXCEPTION
      WHEN no_data_found OR too_many_rows
        OR invalid_text_representation OR numeric_value_out_of_range THEN
        RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
    END
    $$
    """
)

_EXACT_ONE_GUARD = _sql(
    r"""
    CREATE FUNCTION public.idis_task7_current_source_exact_one_v1()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path=pg_catalog,pg_temp
    AS $$
    DECLARE
      scope_tenant uuid;
      scope_deal uuid;
    BEGIN
      IF TG_TABLE_NAME='source_lineage_objects'
        AND TG_OP='INSERT'
        AND pg_catalog.to_jsonb(NEW)->>'write_origin'='BACKFILL'
      THEN
        RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001';
      END IF;
      IF TG_TABLE_NAME='source_lineage_objects' AND TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001';
      END IF;
      IF TG_TABLE_NAME='document_parse_attempts'
        AND TG_OP IN ('INSERT','UPDATE')
        AND pg_catalog.to_jsonb(NEW)->>'write_origin'='BACKFILL'
      THEN
        RAISE EXCEPTION 'WORKER_LEASE_FENCED' USING ERRCODE='40001';
      END IF;
      IF TG_OP='DELETE' THEN
        scope_tenant:=OLD.tenant_id;
        scope_deal:=OLD.deal_id;
      ELSE
        scope_tenant:=NEW.tenant_id;
        scope_deal:=NEW.deal_id;
      END IF;
      IF EXISTS (
        WITH roots AS (
          SELECT
            source_lineage_object_id AS root_id,
            object_kind,
            corpus_receipt_id
          FROM public.source_lineage_objects
          WHERE tenant_id=scope_tenant AND deal_id=scope_deal
        ),
        natives AS (
          SELECT
            source_lineage_object_id AS root_id,
            source_lineage_object_kind AS object_kind,
            source_corpus_receipt_id AS corpus_receipt_id
          FROM public.data_room_packages
          WHERE tenant_id=scope_tenant AND deal_id=scope_deal
            AND source_binding_state='BOUND'
          UNION ALL
          SELECT
            source_lineage_object_id,
            source_lineage_object_kind,
            source_corpus_receipt_id
          FROM public.data_room_package_files
          WHERE tenant_id=scope_tenant AND deal_id=scope_deal
            AND source_binding_state='BOUND'
          UNION ALL
          SELECT
            source_lineage_object_id,
            source_lineage_object_kind,
            source_corpus_receipt_id
          FROM public.document_artifacts
          WHERE tenant_id=scope_tenant AND deal_id=scope_deal
            AND source_binding_state='BOUND'
          UNION ALL
          SELECT
            source_lineage_object_id,
            source_lineage_object_kind,
            source_corpus_receipt_id
          FROM public.documents
          WHERE tenant_id=scope_tenant AND deal_id=scope_deal
            AND source_binding_state='BOUND'
          UNION ALL
          SELECT
            source_lineage_object_id,
            source_lineage_object_kind,
            source_corpus_receipt_id
          FROM public.document_parse_attempts
          WHERE tenant_id=scope_tenant AND deal_id=scope_deal
            AND source_binding_state='BOUND'
          UNION ALL
          SELECT
            source_lineage_object_id,
            source_lineage_object_kind,
            source_corpus_receipt_id
          FROM public.document_spans
          WHERE tenant_id=scope_tenant AND deal_id=scope_deal
            AND source_binding_state='BOUND'
        ),
        census AS (
          SELECT
            COALESCE(roots.root_id,natives.root_id) AS root_id,
            count(roots.root_id) AS root_count,
            count(natives.root_id) AS native_count
          FROM roots
          FULL JOIN natives
            ON natives.root_id=roots.root_id
           AND natives.object_kind=roots.object_kind
           AND natives.corpus_receipt_id=roots.corpus_receipt_id
          GROUP BY COALESCE(roots.root_id,natives.root_id)
        )
        SELECT 1
        FROM census
        WHERE root_count<>1 OR native_count<>1
      ) THEN
        RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
      END IF;
      IF EXISTS (
        SELECT 1
        FROM public.source_lineage_objects AS root
        WHERE root.tenant_id=scope_tenant
          AND root.deal_id=scope_deal
          AND root.write_origin='RUNTIME'
          AND public.idis_task7_source_projection_v1(
                root.source_lineage_object_id
              ) IS DISTINCT FROM pg_catalog.jsonb_build_object(
                'source_lineage_object_id',root.source_lineage_object_id::text,
                'tenant_id',root.tenant_id::text,
                'deal_id',root.deal_id::text,
                'corpus_receipt_id',root.corpus_receipt_id::text,
                'object_kind',root.object_kind,
                'source_version',root.source_version,
                'source_disposition',root.source_disposition,
                'object_identity_sha256',root.object_identity_sha256,
                'content_sha256',root.content_sha256,
                'corpus_receipt_sha256',root.corpus_receipt_sha256,
                'write_origin',root.write_origin
              )
      ) THEN
        RAISE EXCEPTION 'TASK7_SOURCE_TERMINAL_STATE_INVALID' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END
    $$
    """
)

_EXACT_ONE_TRIGGER_STATEMENTS = (
    "CREATE CONSTRAINT TRIGGER ctrg_task7_source_lineage_objects_exact_one "
    "AFTER INSERT OR UPDATE OR DELETE ON public.source_lineage_objects "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
    "public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_data_room_packages_source_exact_one "
    "AFTER INSERT OR UPDATE OR DELETE ON public.data_room_packages "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
    "public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_data_room_package_files_source_exact_one "
    "AFTER INSERT OR UPDATE OR DELETE ON public.data_room_package_files "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
    "public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_document_artifacts_source_exact_one "
    "AFTER INSERT OR UPDATE OR DELETE ON public.document_artifacts "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
    "public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_documents_source_exact_one "
    "AFTER INSERT OR UPDATE OR DELETE ON public.documents "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
    "public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_document_parse_attempts_source_exact_one "
    "AFTER INSERT OR UPDATE OR DELETE ON public.document_parse_attempts "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
    "public.idis_task7_current_source_exact_one_v1()",
    "CREATE CONSTRAINT TRIGGER ctrg_task7_document_spans_source_exact_one "
    "AFTER INSERT OR UPDATE OR DELETE ON public.document_spans "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
    "public.idis_task7_current_source_exact_one_v1()",
)

_REVOKE_HELPERS = (
    "REVOKE ALL ON FUNCTION public.idis_task7_source_canonical_jsonb_text_v1(jsonb) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_source_hash_jsonb_v1(jsonb) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_sha1_v1(bytea) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_uuidv5_v1(uuid,text) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_source_projection_v1(uuid) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.idis_task7_current_source_exact_one_v1() FROM PUBLIC",
)


def build_source_exact_one_statements(v16_row: dict[str, Any]) -> tuple[str, ...]:
    """Return the complete v17 authority-17 statement tuple."""

    if v16_row.get("authority_id") != "current-source-exact-one-v1":
        raise SourceProjectionSqlError("TASK7_V17_SOURCE_EXACT_ONE_BASE_INVALID")
    if len(v16_row.get("target_tuple", ())) != 7:
        raise SourceProjectionSqlError("TASK7_V17_SOURCE_EXACT_ONE_TARGETS_INVALID")
    return (
        _SOURCE_CANONICAL_JSON,
        _SOURCE_HASH_JSON,
        _SHA1,
        _UUID5,
        _SOURCE_PROJECTION,
        _EXACT_ONE_GUARD,
        *_EXACT_ONE_TRIGGER_STATEMENTS,
        *_REVOKE_HELPERS,
    )


def build_source_bound_statements(v16_row: dict[str, Any]) -> tuple[str, ...]:
    """Return authority 22 with the single approved package-file INSERT fence."""

    if v16_row.get("authority_id") != "current-source-bound-v1":
        raise SourceProjectionSqlError("TASK7_V17_SOURCE_BOUND_BASE_INVALID")
    encoded = v16_row.get("body_sql_utf8_hex_tuple")
    if not isinstance(encoded, list) or len(encoded) != 8:
        raise SourceProjectionSqlError("TASK7_V17_SOURCE_BOUND_SHAPE_INVALID")
    statements = [bytes.fromhex(item).decode("utf-8") for item in encoded]
    old_guard = statements[0]
    marker = (
        "BEGIN IF TG_OP='DELETE' THEN IF "
        "public.idis_task7_approved_erasure_context_v1(OLD.tenant_id,OLD.deal_id) "
    )
    if old_guard.count(marker) != 1:
        raise SourceProjectionSqlError("TASK7_V17_SOURCE_BOUND_GUARD_INVALID")
    old_generic = (
        "OR (OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' "
        "AND TG_TABLE_NAME NOT IN ('data_room_packages','document_spans') "
    )
    package_file_transition = (
        "OR (OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' "
        "AND TG_TABLE_NAME='data_room_package_files' AND "
        "(pg_catalog.to_jsonb(NEW)-ARRAY['source_binding_state',"
        "'source_lineage_object_id','source_corpus_receipt_id',"
        "'source_lineage_object_kind','doc_id','document_id']::text[]) "
        "IS DISTINCT FROM "
        "(pg_catalog.to_jsonb(OLD)-ARRAY['source_binding_state',"
        "'source_lineage_object_id','source_corpus_receipt_id',"
        "'source_lineage_object_kind','doc_id','document_id']::text[])) "
    )
    new_generic = (
        package_file_transition
        + "OR (OLD.source_binding_state='UNBOUND' AND NEW.source_binding_state='BOUND' "
        "AND TG_TABLE_NAME NOT IN "
        "('data_room_packages','document_spans','data_room_package_files') "
    )
    if old_guard.count(old_generic) != 1:
        raise SourceProjectionSqlError("TASK7_V17_SOURCE_BOUND_TRANSITION_INVALID")
    old_guard = old_guard.replace(old_generic, new_generic, 1)
    insert_branch = (
        "BEGIN IF TG_OP='INSERT' THEN "
        "IF TG_TABLE_NAME<>'data_room_package_files' "
        "OR NEW.source_binding_state<>'UNBOUND' "
        "OR NEW.source_lineage_object_id IS NOT NULL THEN "
        "RAISE EXCEPTION 'SOURCE_TREE_MUTATED' USING ERRCODE='23514'; END IF; "
        "PERFORM pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended("
        "'task7-source-package-v1:'||NEW.package_id::text,0)); "
        "IF NOT EXISTS (SELECT 1 FROM public.data_room_packages p "
        "WHERE p.tenant_id=NEW.tenant_id AND p.deal_id=NEW.deal_id "
        "AND p.package_id=NEW.package_id) OR EXISTS ("
        "SELECT 1 FROM public.data_room_packages p "
        "WHERE p.tenant_id=NEW.tenant_id AND p.deal_id=NEW.deal_id "
        "AND p.package_id=NEW.package_id "
        "AND (p.status='SEALED' OR p.source_binding_state='BOUND')) THEN "
        "RAISE EXCEPTION 'SOURCE_TREE_MUTATED' USING ERRCODE='23514'; END IF; "
        "RETURN NEW; END IF; "
        "IF TG_TABLE_NAME='data_room_packages' THEN "
        "PERFORM pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended("
        "'task7-source-package-v1:'||OLD.package_id::text,0)); END IF; "
    )
    statements[0] = old_guard.replace(marker, insert_branch + marker.removeprefix("BEGIN "), 1)
    old_trigger = (
        "CREATE TRIGGER trg_task7_data_room_package_files_source_bound "
        "BEFORE UPDATE OR DELETE ON public.data_room_package_files "
    )
    new_trigger = (
        "CREATE TRIGGER trg_task7_data_room_package_files_source_bound "
        "BEFORE INSERT OR UPDATE OR DELETE ON public.data_room_package_files "
    )
    if statements[2].count(old_trigger) != 1:
        raise SourceProjectionSqlError("TASK7_V17_SOURCE_BOUND_TRIGGER_INVALID")
    statements[2] = statements[2].replace(old_trigger, new_trigger, 1)
    return tuple(statements)


def projection_function_signatures() -> tuple[str, ...]:
    """Return the helper signatures that the v17 ACL builder must own."""

    return (
        "public.idis_task7_source_canonical_jsonb_text_v1(jsonb)",
        "public.idis_task7_source_hash_jsonb_v1(jsonb)",
        "public.idis_task7_sha1_v1(bytea)",
        "public.idis_task7_uuidv5_v1(uuid,text)",
        "public.idis_task7_source_projection_v1(uuid)",
    )


def projection_read_column_closure() -> dict[str, tuple[str, ...]]:
    """Return the exact native/source columns read by the projection helper."""

    return {
        "source_lineage_objects": (
            "source_lineage_object_id",
            "tenant_id",
            "deal_id",
            "corpus_receipt_id",
            "object_kind",
            "source_version",
            "source_disposition",
            "object_identity_sha256",
            "content_sha256",
            "corpus_receipt_sha256",
            "write_origin",
        ),
        "data_room_packages": (
            "package_id",
            "tenant_id",
            "deal_id",
            "status",
            "metadata",
            "source_binding_state",
            "source_lineage_object_id",
        ),
        "data_room_package_files": (
            "file_entry_id",
            "tenant_id",
            "package_id",
            "deal_id",
            "sequence",
            "path_hash",
            "extension",
            "sha256",
            "file_status",
            "support_status",
            "triage_status",
            "parse_status",
            "reason_codes",
            "error_codes",
            "doc_id",
            "document_id",
            "storage_uri",
            "source_binding_state",
            "source_lineage_object_id",
            "source_corpus_receipt_id",
        ),
        "document_artifacts": (
            "doc_id",
            "tenant_id",
            "deal_id",
            "doc_type",
            "title",
            "source_system",
            "version_id",
            "ingested_at",
            "sha256",
            "uri",
            "metadata",
            "source_binding_state",
            "source_lineage_object_id",
            "source_corpus_receipt_id",
        ),
        "documents": (
            "document_id",
            "tenant_id",
            "deal_id",
            "doc_id",
            "doc_type",
            "parse_status",
            "metadata",
            "source_binding_state",
            "source_lineage_object_id",
            "source_corpus_receipt_id",
        ),
        "document_parse_attempts": (
            "parse_attempt_id",
            "tenant_id",
            "deal_id",
            "document_id",
            "attempt_ordinal",
            "parser_name",
            "parser_version",
            "input_sha256",
            "receipt_sha256",
            "status",
            "started_at",
            "finished_at",
            "source_binding_state",
            "source_lineage_object_id",
            "source_corpus_receipt_id",
            "write_origin",
        ),
        "document_spans": (
            "span_id",
            "tenant_id",
            "deal_id",
            "document_id",
            "span_type",
            "locator",
            "content_hash",
            "parse_attempt_id",
            "source_binding_state",
            "source_lineage_object_id",
            "source_corpus_receipt_id",
        ),
    }
