import { supabaseUrl, supabaseKey } from "./supabase.js";

/** Upload one file to a private bucket with real progress (supabase-js has no upload progress), using the signed-in user's token. */
export function uploadWithProgress(bucket, path, file, accessToken, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${supabaseUrl}/storage/v1/object/${bucket}/${path}`);
    xhr.setRequestHeader("apikey", supabaseKey);
    xhr.setRequestHeader("Authorization", `Bearer ${accessToken}`);
    xhr.setRequestHeader("x-upsert", "false");
    xhr.setRequestHeader("Content-Type", file.type || "video/mp4");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onerror = () => reject(new Error("The upload failed: no connection. Try again on a better network."));
    xhr.ontimeout = () => reject(new Error("The upload timed out. Try again."));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      let msg = "";
      try { msg = JSON.parse(xhr.responseText).message || ""; } catch (e) { /* not json */ }
      reject(new Error(xhr.status === 413 ? "The server refused the file because it is too large." : `The upload was refused (${xhr.status}). ${msg}`.trim()));
    };
    xhr.send(file);
  });
}
