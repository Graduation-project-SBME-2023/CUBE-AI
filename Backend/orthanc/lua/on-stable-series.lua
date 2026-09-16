-- Orthanc Lua script to notify MONAI/FastAPI when resources change.
--
-- This is a best-effort, low-latency notification path only. It is NOT the
-- system of record for keeping Postgres in sync with Orthanc: the
-- application-api process also runs a periodic full reconciliation
-- (sync_all_from_orthanc, see main.py's lifespan) specifically because a
-- webhook call made while application-api is restarting is lost with no
-- retry. If you disable or slow down that periodic reconciliation, a lost
-- webhook here becomes a permanently missed sync.

local WEBHOOK_URL = 'http://application-api:8001/sync/orthanc-webhook'

-- Read from the same ORTHANC_WEBHOOK_TOKEN the application-api validates
-- against (see database_audit.md D-30), so the two never drift out of sync.
-- Never hardcode a real token value here: this file is tracked in git.
-- Empty/unset matches an application-api deployment that has
-- ORTHANC_WEBHOOK_TOKEN unset (no auth enforced).
local WEBHOOK_TOKEN = os.getenv('ORTHANC_WEBHOOK_TOKEN') or ''

local function webhook_headers()
    local headers = { ['Content-Type'] = 'application/json' }
    if WEBHOOK_TOKEN ~= '' then
        headers['X-Webhook-Token'] = WEBHOOK_TOKEN
    end
    return headers
end

local function main_tag(resource, tag_name, fallback)
    local tags = resource['MainDicomTags'] or {}
    return tags[tag_name] or fallback
end

local function post_webhook(payload, description)
    -- Best-effort: MONAI Label may be busy (e.g. running inference), and
    -- application-api may be restarting. A failure here is expected and is
    -- covered by the periodic reconciliation pass described above, so it is
    -- not fatal to this script — but it must be visible in Orthanc's own
    -- logs rather than silently discarded, which the previous bare pcall()
    -- did (it also never used the `description` argument at all).
    local ok, err = pcall(function()
        HttpPost(WEBHOOK_URL, DumpJson(payload, true), webhook_headers())
    end)
    if not ok then
        PrintToConsole('Webhook POST failed for ' .. description .. ': ' .. tostring(err))
    end
end

local function notify_deleted(resource_id, level)
    post_webhook({
        change_type = 'deleted',
        resource_id = resource_id,
        level = level
    }, 'deleted ' .. level .. ' ' .. resource_id)
end

function OnStableSeries(seriesId, tags, metadata)
    local series = ParseJson(RestApiGet('/series/' .. seriesId))
    local modality = main_tag(series, 'Modality', 'UNKNOWN')
    local series_uid = main_tag(series, 'SeriesInstanceUID', seriesId)

    post_webhook({
        change_type = 'stable_series',
        resource_id = seriesId,
        level = 'series',
        modality = modality,
        series_uid = series_uid
    }, 'stable series ' .. series_uid)
end

function OnStableStudy(studyId, tags, metadata)
    local study = ParseJson(RestApiGet('/studies/' .. studyId))
    local study_uid = main_tag(study, 'StudyInstanceUID', studyId)

    post_webhook({
        change_type = 'stable_study',
        resource_id = studyId,
        level = 'study',
        study_uid = study_uid
    }, 'stable study ' .. study_uid)
end

function OnDeletedPatient(patientId)
    notify_deleted(patientId, 'patient')
end

function OnDeletedStudy(studyId)
    notify_deleted(studyId, 'study')
end

function OnDeletedSeries(seriesId)
    notify_deleted(seriesId, 'series')
end
