-- WorldKit: enforce the upload limits on the server too, not only in the web app and the worker.
-- videos: phone uploads, at most 50 MB, video types only. results: what the laptop worker uploads (GLB, STL, JSON, PNG/JPEG), at most 50 MB.
-- Storage refuses anything else with 413 / 415 before it is stored.
update storage.buckets
   set file_size_limit = 52428800,
       allowed_mime_types = array['video/mp4', 'video/quicktime', 'video/x-m4v', 'video/webm', 'video/3gpp']
 where id = 'videos';

update storage.buckets
   set file_size_limit = 52428800,
       allowed_mime_types = array['model/gltf-binary', 'model/stl', 'application/json', 'image/png', 'image/jpeg']
 where id = 'results';
