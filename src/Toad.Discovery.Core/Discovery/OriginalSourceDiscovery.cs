namespace Toad.Discovery.Core.Discovery;

public sealed record OriginalSourceCandidate(string Path, string Origin, int Priority = 0);

public interface IOriginalSourceLocator
{
    IEnumerable<OriginalSourceCandidate> FindCandidates();
}

public sealed class KnownDirectorySourceLocator(
    IEnumerable<OriginalSourceCandidate> candidates) : IOriginalSourceLocator
{
    public IEnumerable<OriginalSourceCandidate> FindCandidates() => candidates
        .Where(candidate => Directory.Exists(candidate.Path))
        .OrderByDescending(candidate => candidate.Priority)
        .ThenBy(candidate => candidate.Path, StringComparer.OrdinalIgnoreCase);
}

public sealed class CompositeSourceLocator(
    IEnumerable<IOriginalSourceLocator> locators) : IOriginalSourceLocator
{
    public IEnumerable<OriginalSourceCandidate> FindCandidates() => locators
        .SelectMany(locator => locator.FindCandidates())
        .DistinctBy(candidate => Path.GetFullPath(candidate.Path), StringComparer.OrdinalIgnoreCase)
        .OrderByDescending(candidate => candidate.Priority);
}
