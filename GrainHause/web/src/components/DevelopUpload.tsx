import { ArrowLeft } from "lucide-react";
import { PhotoDropzone } from "@/components/ui/file-upload-paste";

export function DevelopUpload({
  first,
  onDevelop,
  onBack,
}: {
  first: boolean;
  onDevelop: (files: File[]) => void;
  onBack?: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-background text-foreground">
      <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col justify-center px-6 py-16">
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            className="mb-8 flex w-fit items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to your photos
          </button>
        )}
        <div className="mb-8 flex items-center gap-2">
          <img src="/icon.png" alt="" width={28} height={28} className="size-7 rounded-md" />
          <span className="text-sm font-bold tracking-[0.25em]">GRAINHAUSE</span>
        </div>
        <h1 className="text-4xl font-semibold tracking-tight">
          {first ? "Develop your first image" : "New image"}
        </h1>
        <p className="mt-3 mb-8 max-w-md text-muted-foreground">
          {first
            ? "Upload your first image to develop. RAW, TIFF, JPEG, PNG, HEIC and WebP all work, and the file stays on this computer."
            : "Add another photo to the strip, then develop it with the same tools."}
        </p>
        <PhotoDropzone developLabel={first ? "Develop" : "Add photos"} onDevelop={onDevelop} />
      </div>
    </div>
  );
}
