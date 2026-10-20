"use client";

import { FileUpload } from "@ark-ui/react/file-upload";
import { Clipboard, FileText, X } from "lucide-react";
import { useEffect, useState } from "react";

const ACCEPT =
  "image/*,.dng,.cr2,.cr3,.nef,.arw,.raf,.orf,.rw2,.pef,.srw,.3fr,.iiq,.rwl,.nrw,.erf,.kdc,.dcr,.mos,.mrw,.x3f,.heic,.heif,.hif,.tif,.tiff";

function PasteBridge({
  setFiles,
  acceptedFiles,
  maxFiles,
  onPasted,
}: {
  setFiles: (files: File[]) => void;
  acceptedFiles: File[];
  maxFiles: number;
  onPasted: () => void;
}) {
  useEffect(() => {
    const handlePaste = (event: ClipboardEvent) => {
      const clipboardData = event.clipboardData;
      if (!clipboardData?.files.length) return;
      const incoming = [...clipboardData.files];
      const room = Math.max(0, maxFiles - acceptedFiles.length);
      if (!room) return;
      event.preventDefault();
      setFiles([...acceptedFiles, ...incoming.slice(0, room)]);
      onPasted();
    };
    document.addEventListener("paste", handlePaste);
    return () => document.removeEventListener("paste", handlePaste);
  }, [acceptedFiles, maxFiles, onPasted, setFiles]);
  return null;
}

export function PhotoDropzone({
  maxFiles = 40,
  developLabel = "Develop",
  onDevelop,
}: {
  maxFiles?: number;
  developLabel?: string;
  onDevelop: (files: File[]) => void;
}) {
  const [isPasted, setIsPasted] = useState(false);

  const flashPasted = () => {
    setIsPasted(true);
    window.setTimeout(() => setIsPasted(false), 2000);
  };

  return (
    <FileUpload.Root
      maxFiles={maxFiles}
      accept={ACCEPT}
      className="w-full"
    >
      <FileUpload.Context>
        {({ acceptedFiles, rejectedFiles, setFiles }) => (
          <div className="space-y-4">
            <PasteBridge
              setFiles={setFiles}
              acceptedFiles={acceptedFiles}
              maxFiles={maxFiles}
              onPasted={flashPasted}
            />

            <FileUpload.Dropzone className="relative flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed border-border bg-card/60 px-6 py-12 transition-colors hover:bg-card focus:outline-hidden focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-border bg-background">
                <Clipboard className="h-5 w-5 text-muted-foreground" />
              </div>
              <div className="space-y-2 text-center">
                <h3 className="text-sm font-medium text-foreground">Upload files</h3>
                <p className="text-sm text-muted-foreground">Drag and drop, click to browse, or paste</p>
                <div className="flex items-center justify-center gap-4 text-xs text-muted-foreground">
                  <span>⌘V or Ctrl+V to paste</span>
                  <span>·</span>
                  <span>Up to {maxFiles} photos</span>
                </div>
              </div>
              {isPasted && (
                <div className="absolute inset-0 flex items-center justify-center rounded-xl border-2 border-primary/60 bg-primary/15">
                  <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <Clipboard className="h-4 w-4" />
                    Files pasted
                  </div>
                </div>
              )}
            </FileUpload.Dropzone>

            {rejectedFiles.length > 0 && (
              <p className="text-sm text-destructive">
                {rejectedFiles.length === 1
                  ? "That file isn't a photo this uploader can take."
                  : `${rejectedFiles.length} files were skipped. Use a photo or RAW file.`}
              </p>
            )}

            {acceptedFiles.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-sm font-medium text-foreground">
                  Ready to develop ({acceptedFiles.length})
                </h4>
                <FileUpload.ItemGroup>
                  <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                    {acceptedFiles.map((file) => (
                      <FileUpload.Item key={`${file.name}-${file.size}-${file.lastModified}`} file={file}>
                        <div className="flex items-center gap-3 rounded-lg border border-border bg-background p-3">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-border bg-card">
                            {file.type.startsWith("image/") ? (
                              <FileUpload.ItemPreview type="image/*">
                                <FileUpload.ItemPreviewImage className="h-full w-full object-cover" />
                              </FileUpload.ItemPreview>
                            ) : (
                              <FileText className="h-4 w-4 text-muted-foreground" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <FileUpload.ItemName className="truncate text-sm font-medium text-foreground" />
                            <FileUpload.ItemSizeText className="text-xs text-muted-foreground" />
                          </div>
                          <FileUpload.ItemDeleteTrigger className="flex h-6 w-6 shrink-0 items-center justify-center text-muted-foreground hover:text-foreground">
                            <X className="h-4 w-4" />
                          </FileUpload.ItemDeleteTrigger>
                        </div>
                      </FileUpload.Item>
                    ))}
                  </div>
                </FileUpload.ItemGroup>

                <div className="flex items-center gap-3 pt-1">
                  <button
                    type="button"
                    onClick={() => onDevelop(acceptedFiles)}
                    className="rounded-full bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
                  >
                    {developLabel}
                  </button>
                  <FileUpload.ClearTrigger className="rounded-full border border-border px-4 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground">
                    Clear all
                  </FileUpload.ClearTrigger>
                </div>
              </div>
            )}

          </div>
        )}
      </FileUpload.Context>
      <FileUpload.HiddenInput />
    </FileUpload.Root>
  );
}
