import { cn } from "@/lib/utils"

type TextareaProps = React.ComponentProps<"textarea">

function Textarea({ className, ...props }: TextareaProps) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "min-h-[88px] w-full rounded-[2px] border border-border bg-raised px-2.5 py-2 font-sans text-[12px] leading-[1.4] text-foreground outline-none placeholder:text-faint focus-visible:border-primary",
        className,
      )}
      {...props}
    />
  )
}

export { Textarea }
