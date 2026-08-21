import {
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { GripVertical, ListOrdered } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/common/EmptyState";
import { StatusBadge } from "@/components/status/StatusBadge";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type JobSummary, type QueueResponse, api } from "@/lib/api";
import { jobDisplayTitle } from "@/lib/format";
import { queryKeys } from "@/lib/query-keys";

function SortableQueueRow({ job, index }: { job: JobSummary; index: number }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: job.id,
  });

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className="bg-card flex items-center gap-2 rounded-lg border p-2"
      data-dragging={isDragging}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label="Drag to reorder"
        className="text-muted-foreground hover:text-foreground cursor-grab touch-none active:cursor-grabbing"
      >
        <GripVertical className="size-4" />
      </button>
      <span className="text-muted-foreground w-5 shrink-0 text-right text-xs tabular-nums">
        {index + 1}
      </span>
      <Link
        to="/jobs/$jobId"
        params={{ jobId: String(job.id) }}
        className="hover:text-primary min-w-0 flex-1 truncate text-sm"
      >
        {jobDisplayTitle(job.title, job.year)}
      </Link>
      <StatusBadge status={job.status} size="sm" />
    </div>
  );
}

export function QueueList() {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: queryKeys.queue.list(),
    queryFn: api.queue.get,
  });

  const reorder = useMutation({
    mutationFn: (jobIds: number[]) => api.queue.reorder(jobIds),
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.message : "Reorder failed.");
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.list() });
    },
  });

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id || !data) return;

    const items = data.items;
    const oldIndex = items.findIndex((j) => j.id === active.id);
    const newIndex = items.findIndex((j) => j.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;

    const reordered = [...items];
    const [moved] = reordered.splice(oldIndex, 1);
    reordered.splice(newIndex, 0, moved);

    queryClient.setQueryData<QueueResponse>(queryKeys.queue.list(), { items: reordered });
    reorder.mutate(reordered.map((j) => j.id));
  }

  if (isPending) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-11 w-full" />
        ))}
      </div>
    );
  }

  if (!data || data.items.length === 0) {
    return <EmptyState icon={ListOrdered} title="Queue is empty" className="border-none p-4" />;
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={data.items.map((j) => j.id)} strategy={verticalListSortingStrategy}>
        <div className="space-y-2">
          {data.items.map((job, index) => (
            <SortableQueueRow key={job.id} job={job} index={index} />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
