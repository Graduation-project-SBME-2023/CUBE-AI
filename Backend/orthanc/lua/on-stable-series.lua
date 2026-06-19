-- Orthanc Lua script to notify MONAI/FastAPI when resources change.

local WEBHOOK_URL = 'http://monailabel:8000/sync/orthanc-webhook'
local JSON_HEADERS = { ['Content-Type'] = 'application/json' }

local function main_tag(resource, tag_name, fallback)
    local tags = resource['MainDicomTags'] or {}
    return tags[tag_name] or fallback
end

local function post_webhook(payload, description)
    local success = pcall(function()
        HttpPost(WEBHOOK_URL, DumpJson(payload, true), JSON_HEADERS)
    end)

    if not success then
        print('Failed to notify MONAI/FastAPI: ' .. description)
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
