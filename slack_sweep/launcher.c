#include <errno.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>

#ifndef SWEEP_PYTHON
#error "build with -DSWEEP_PYTHON=\"/path/to/python3\""
#endif
#ifndef SWEEP_ENTRY
#error "build with -DSWEEP_ENTRY=\"/path/to/bin/obsidian-slack-sweep\""
#endif

extern char **environ;

static int inherited(const char *entry) {
    return strncmp(entry, "DYLD_", 5) != 0 && strncmp(entry, "PYTHON", 6) != 0;
}

int main(int argc, char *argv[]) {
    size_t count = 0;
    for (char **e = environ; *e; e++) count++;
    char **env = calloc(count + 1, sizeof *env);
    char **args = calloc((size_t)argc + 4, sizeof *args);
    if (!env || !args) return 1;

    size_t kept = 0;
    for (char **e = environ; *e; e++)
        if (inherited(*e)) env[kept++] = *e;

    args[0] = SWEEP_PYTHON;
    args[1] = "-E";
    args[2] = "-s";
    args[3] = SWEEP_ENTRY;
    for (int i = 1; i < argc; i++) args[i + 3] = argv[i];

    pid_t pid;
    int rc = posix_spawn(&pid, SWEEP_PYTHON, NULL, NULL, args, env);
    if (rc != 0) {
        fprintf(stderr, "obsidian-slack-sweep: cannot start %s: %s\n", SWEEP_PYTHON, strerror(rc));
        return 127;
    }
    int status;
    while (waitpid(pid, &status, 0) < 0)
        if (errno != EINTR) return 1;
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return 128 + WTERMSIG(status);
}
