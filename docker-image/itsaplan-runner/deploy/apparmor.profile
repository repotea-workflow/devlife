# Based on Moby container protection; user namespaces and mounts are required by
# the nested coding-agent sandbox. No host SYS_ADMIN capability is granted.
#include <tunables/global>
profile itsaplan-runner flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  network,
  capability,
  file,
  userns,
  mount,
  umount,
  pivot_root,
  signal (send,receive) peer=itsaplan-runner,
  signal (receive) peer=unconfined,
  signal (receive) peer=runc,
  signal (receive) peer=docker-default,
  ptrace (trace,tracedby,read,readby) peer=itsaplan-runner,
  deny /proc/{bus,fs,irq,sys}/** wklx,
  deny /proc/sysrq-trigger rwklx,
  deny /proc/kcore rwklx,
  deny /proc/{acpi,asound,interrupts,keys,latency_stats,sched_debug,scsi,timer_list}{,/**} rwklx,
  deny /sys/** wklx,
  deny /sys/firmware/** rwklx,
  deny /sys/devices/virtual/powercap/** rwklx,
  deny /sys/kernel/security/** rwklx,
}
