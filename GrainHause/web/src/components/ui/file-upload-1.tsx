"use client";

import { FileUpload } from "@ark-ui/react/file-upload";
import { User } from "lucide-react";

export default function Basic() {
  return (
    <FileUpload.Root
      maxFiles={1}
      accept="image/*"
      className="flex flex-col items-start gap-3"
    >
      <FileUpload.Context>
        {({ acceptedFiles }) => (
          <>
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-xl border border-border bg-card">
                {acceptedFiles.length > 0 ? (
                  <FileUpload.ItemGroup>
                    <FileUpload.Item file={acceptedFiles[0]}>
                      <FileUpload.ItemPreview type="image/*">
                        <FileUpload.ItemPreviewImage className="h-full w-full object-cover" />
                      </FileUpload.ItemPreview>
                    </FileUpload.Item>
                  </FileUpload.ItemGroup>
                ) : (
                  <User className="h-5 w-5 text-muted-foreground" />
                )}
              </div>

              <FileUpload.Trigger className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-hidden focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background">
                {acceptedFiles.length > 0 ? "Change image" : "Upload image"}
              </FileUpload.Trigger>
            </div>

            {acceptedFiles.length > 0 && (
              <FileUpload.ItemGroup>
                <FileUpload.Item file={acceptedFiles[0]} className="flex items-center gap-2">
                  <FileUpload.ItemName className="text-sm text-muted-foreground" />
                  <FileUpload.ItemDeleteTrigger className="text-sm text-destructive hover:text-destructive/80">
                    Remove
                  </FileUpload.ItemDeleteTrigger>
                </FileUpload.Item>
              </FileUpload.ItemGroup>
            )}
          </>
        )}
      </FileUpload.Context>

      <FileUpload.HiddenInput />
    </FileUpload.Root>
  );
}
