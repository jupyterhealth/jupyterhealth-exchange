"""
Study-related query tools with permission enforcement

Uses connection pooling for efficient database access and context
managers for automatic resource cleanup.
"""

import json
import logging
from typing import List
from mcp.types import TextContent
import psycopg2

from auth import AuthContext
from db_pool import get_db_connection

logger = logging.getLogger(__name__)


def get_study_count(auth: AuthContext) -> List[TextContent]:
    """
    Count total studies accessible to the authenticated user

    Args:
        auth: Authenticated user context

    Returns:
        List of TextContent with study count
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if auth.is_superuser:
                    # Superusers see all studies
                    cursor.execute("SELECT COUNT(*) FROM core_study")
                else:
                    # Count studies user can access via their practitioner organizations
                    cursor.execute(
                        """
                        SELECT COUNT(DISTINCT cs.id)
                        FROM core_study cs
                        JOIN core_organization co ON co.id = cs.organization_id
                        JOIN core_practitionerorganization cpo ON cpo.organization_id = co.id
                        JOIN core_practitioner cp ON cp.id = cpo.practitioner_id
                        WHERE cp.jhe_user_id = %s
                    """,
                        (auth.user_id,),
                    )

                result = cursor.fetchone()
                count = result[0] if result else 0

        return [TextContent(type="text", text=f"You have access to {count} studies")]

    except psycopg2.Error as e:
        logger.error(f"Database error in get_study_count: {e}")
        return [TextContent(type="text", text=f"❌ Database error: {str(e)}")]


def list_studies(auth: AuthContext) -> List[TextContent]:
    """
    List all studies accessible to the authenticated user

    Args:
        auth: Authenticated user context

    Returns:
        List of TextContent with study IDs and names
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if auth.is_superuser:
                    # Superusers see all studies
                    cursor.execute(
                        """
                        SELECT s.id, s.name, o.name as organization_name
                        FROM core_study s
                        JOIN core_organization o ON o.id = s.organization_id
                        ORDER BY s.id
                    """
                    )
                else:
                    # List studies user can access via their practitioner organizations
                    cursor.execute(
                        """
                        SELECT DISTINCT s.id, s.name, o.name as organization_name
                        FROM core_study s
                        JOIN core_organization o ON o.id = s.organization_id
                        JOIN core_practitionerorganization cpo ON cpo.organization_id = o.id
                        JOIN core_practitioner cp ON cp.id = cpo.practitioner_id
                        WHERE cp.jhe_user_id = %s
                        ORDER BY s.id
                    """,
                        (auth.user_id,),
                    )

                rows = cursor.fetchall()

        if not rows:
            return [TextContent(type="text", text="No studies found")]

        # Format results
        result_lines = [f"Accessible studies ({len(rows)}):\n"]
        for study_id, name, org_name in rows:
            result_lines.append(f"  Study {study_id}: {name} (Organization: {org_name})")

        return [TextContent(type="text", text="\n".join(result_lines))]

    except psycopg2.Error as e:
        logger.error(f"Database error in list_studies: {e}")
        return [TextContent(type="text", text=f"❌ Database error: {str(e)}")]


def get_patient_demographics(auth: AuthContext, study_id: int) -> List[TextContent]:
    """
    Get patient demographics for a specific study

    Args:
        auth: Authenticated user context
        study_id: Study identifier

    Returns:
        List of TextContent with patient demographics

    Raises:
        PermissionError: If user doesn't have access to the study
    """
    # Check permission
    if not auth.can_access_study(study_id):
        return [TextContent(type="text", text=f"❌ Access denied to study {study_id}")]

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Query patient demographics with proper JHE schema
                cursor.execute(
                    """
                    SELECT
                        p.id,
                        EXTRACT(YEAR FROM AGE(p.birth_date)) as age,
                        u.email
                    FROM core_patient p
                    LEFT JOIN core_jheuser u ON u.id = p.jhe_user_id
                    JOIN core_studypatient sp ON sp.patient_id = p.id
                    WHERE sp.study_id = %s
                    ORDER BY p.id
                """,
                    (study_id,),
                )

                rows = cursor.fetchall()

        if not rows:
            return [TextContent(type="text", text=f"No patients found in study {study_id}")]

        # Format results
        result_lines = [f"Study {study_id} - {len(rows)} patient(s):\n"]
        for row in rows:
            patient_id, age, email = row
            age_str = f"{int(age)} years" if age else "N/A"
            email_str = email if email else "No email"
            result_lines.append(f"  Patient {patient_id}: Age {age_str}, Email: {email_str}")

        return [TextContent(type="text", text="\n".join(result_lines))]

    except psycopg2.Error as e:
        logger.error(f"Database error in get_patient_demographics: {e}")
        return [TextContent(type="text", text=f"❌ Database error: {str(e)}")]


def get_study_metadata(auth: AuthContext, study_id: int) -> List[TextContent]:
    """
    Get metadata about a specific study

    Args:
        auth: Authenticated user context
        study_id: Study identifier

    Returns:
        List of TextContent with study metadata

    Raises:
        PermissionError: If user doesn't have access to the study
    """
    # Check permission
    if not auth.can_access_study(study_id):
        return [TextContent(type="text", text=f"❌ Access denied to study {study_id}")]

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Query study metadata with organization and counts
                cursor.execute(
                    """
                    SELECT
                        s.id,
                        s.name,
                        s.description,
                        o.name as organization_name,
                        o.type as organization_type,
                        (SELECT COUNT(*) FROM core_studypatient WHERE study_id = s.id) as patient_count,
                        (SELECT COUNT(*) FROM core_observation obs
                         JOIN core_studypatient sp ON sp.patient_id = obs.subject_patient_id
                         WHERE sp.study_id = s.id) as observation_count
                    FROM core_study s
                    JOIN core_organization o ON o.id = s.organization_id
                    WHERE s.id = %s
                """,
                    (study_id,),
                )

                row = cursor.fetchone()

        if not row:
            return [TextContent(type="text", text=f"Study {study_id} not found")]

        study_id, name, description, org_name, org_type, patient_count, obs_count = row

        # Format result
        result = f"""Study ID: {study_id}
Name: {name}
Description: {description or 'N/A'}
Organization: {org_name} ({org_type})
Patients: {patient_count}
Observations: {obs_count}
"""

        return [TextContent(type="text", text=result.strip())]

    except psycopg2.Error as e:
        logger.error(f"Database error in get_study_metadata: {e}")
        return [TextContent(type="text", text=f"❌ Database error: {str(e)}")]


def get_patient_observations(auth: AuthContext, patient_id: int, limit: int = 10) -> List[TextContent]:
    """
    Get FHIR observations for a specific patient

    Args:
        auth: Authenticated user context
        patient_id: Patient identifier
        limit: Maximum number of observations to return (default 10)

    Returns:
        List of TextContent with observation data including FHIR/OMH JSON

    Raises:
        PermissionError: If user doesn't have access to the patient
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # First check if user has access to this patient via their studies
                if not auth.is_superuser:
                    cursor.execute(
                        """
                        SELECT COUNT(*)
                        FROM core_patient p
                        JOIN core_studypatient sp ON sp.patient_id = p.id
                        JOIN core_study s ON s.id = sp.study_id
                        JOIN core_organization o ON o.id = s.organization_id
                        JOIN core_practitionerorganization cpo ON cpo.organization_id = o.id
                        JOIN core_practitioner cp ON cp.id = cpo.practitioner_id
                        WHERE p.id = %s AND cp.jhe_user_id = %s
                    """,
                        (patient_id, auth.user_id),
                    )

                    result = cursor.fetchone()
                    if not result or result[0] == 0:
                        return [TextContent(type="text", text=f"❌ Access denied to patient {patient_id}")]

                # Fetch observations with FHIR/OMH data
                cursor.execute(
                    """
                    SELECT
                        o.id,
                        o.status,
                        o.last_updated,
                        o.value_attachment_data,
                        cc.coding_system,
                        cc.coding_code,
                        cc.text,
                        ds.name as data_source_name
                    FROM core_observation o
                    LEFT JOIN core_codeableconcept cc ON cc.id = o.codeable_concept_id
                    LEFT JOIN core_datasource ds ON ds.id = o.data_source_id
                    WHERE o.subject_patient_id = %s
                    ORDER BY o.last_updated DESC
                    LIMIT %s
                """,
                    (patient_id, limit),
                )

                rows = cursor.fetchall()

        if not rows:
            return [TextContent(type="text", text=f"No observations found for patient {patient_id}")]

        # Format results
        result_lines = [f"Patient {patient_id} - {len(rows)} observation(s) (showing most recent {limit}):\n"]

        for idx, row in enumerate(rows, 1):
            obs_id, status, last_updated, value_data, coding_system, coding_code, coding_display, data_source = row

            result_lines.append(f"\n--- Observation {idx} (ID: {obs_id}) ---")
            result_lines.append(f"Status: {status}")
            result_lines.append(f"Code: {coding_display or 'N/A'} ({coding_system}#{coding_code})")
            result_lines.append(f"Data Source: {data_source or 'N/A'}")
            result_lines.append(f"Last Updated: {last_updated}")

            if value_data:
                # Pretty print the JSONB data
                result_lines.append("FHIR/OMH Data:")
                formatted_json = json.dumps(value_data, indent=2)
                result_lines.append(formatted_json)

        return [TextContent(type="text", text="\n".join(result_lines))]

    except psycopg2.Error as e:
        logger.error(f"Database error in get_patient_observations: {e}")
        return [TextContent(type="text", text=f"❌ Database error: {str(e)}")]
