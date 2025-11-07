-- Orthanc Lua script to notify MONAI/FastAPI when series or study is stable
-- This script triggers automatically when a series or study upload is complete

function OnStableSeries(seriesId, tags, metadata)
    -- Get series information from Orthanc
    local series = ParseJson(RestApiGet('/series/' .. seriesId))
    local modality = series['MainDicomTags']['Modality'] or 'UNKNOWN'
    local seriesUID = series['MainDicomTags']['SeriesInstanceUID']

    print('🔔 Stable series detected: ' .. seriesUID .. ' (Modality: ' .. modality .. ')')

    -- Prepare webhook payload
    local payload = {
        change_type = 'stable_series',
        resource_id = seriesId,
        level = 'series',
        modality = modality,
        series_uid = seriesUID
    }

    -- Send webhook to MONAI/FastAPI backend (use Docker service name)
    local success, response = pcall(function()
        return HttpPost(
            'http://monailabel:8000/sync/orthanc-webhook',  -- updated service name
            DumpJson(payload, true),
            { ['Content-Type'] = 'application/json' }
        )
    end)

    if success and response then
        print('✅ MONAI/FastAPI notified successfully for series: ' .. seriesUID)
    else
        print('❌ Failed to notify MONAI/FastAPI for series: ' .. seriesUID)
        -- Orthanc continues even if webhook fails
    end
end

function OnStableStudy(studyId, tags, metadata)
    -- Get study information
    local study = ParseJson(RestApiGet('/studies/' .. studyId))
    local studyUID = study['MainDicomTags']['StudyInstanceUID']

    print('🔔 Stable study detected: ' .. studyUID)

    -- Prepare webhook payload
    local payload = {
        change_type = 'stable_study',
        resource_id = studyId,
        level = 'study',
        study_uid = studyUID
    }

    -- Send webhook to MONAI/FastAPI backend
    pcall(function()
        HttpPost(
            'http://monailabel:8000/sync/orthanc-webhook',  -- updated service name
            DumpJson(payload, true),
            { ['Content-Type'] = 'application/json' }
        )
    end)
end

-- Optional: Log when script is loaded
print('📜 Orthanc Lua webhook script loaded successfully')
