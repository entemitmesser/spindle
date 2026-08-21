import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { useJobActions } from "@/hooks/useJobActions";

interface JobDeleteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobTitle: string;
  deleteJob: ReturnType<typeof useJobActions>["deleteJob"];
}

export function JobDeleteDialog({ open, onOpenChange, jobTitle, deleteJob }: JobDeleteDialogProps) {
  const [purgeFiles, setPurgeFiles] = useState(false);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete “{jobTitle}”?</DialogTitle>
          <DialogDescription>
            This removes the job and its history. This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <Switch id="purge-files" checked={purgeFiles} onCheckedChange={setPurgeFiles} />
          <Label htmlFor="purge-files">Also delete ripped/transcoded files on disk</Label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={deleteJob.isPending}
            onClick={() => {
              deleteJob.mutate(purgeFiles, { onSuccess: () => onOpenChange(false) });
            }}
          >
            Delete job
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
