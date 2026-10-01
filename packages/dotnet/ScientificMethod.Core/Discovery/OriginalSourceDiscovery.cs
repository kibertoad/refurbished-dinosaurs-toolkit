namespace ScientificMethod.Core.Discovery;

/// <summary>A place the original might be installed.</summary>
/// <param name="Path">The directory.</param>
/// <param name="Origin">Where the candidate came from, for display, such as a store name.</param>
/// <param name="Priority">Higher candidates are offered first.</param>
public sealed record OriginalSourceCandidate(string Path, string Origin, int Priority = 0);

/// <summary>Finds directories that may hold the original.</summary>
public interface IOriginalSourceLocator
{
    /// <summary>Returns candidates in the order to offer them.</summary>
    IEnumerable<OriginalSourceCandidate> FindCandidates();
}

/// <summary>Offers a fixed list of directories, keeping those that exist.</summary>
/// <param name="candidates">The directories to check.</param>
public sealed class KnownDirectorySourceLocator(
    IEnumerable<OriginalSourceCandidate> candidates) : IOriginalSourceLocator
{
    /// <summary>Existing candidates, highest priority first, then by path ignoring case.</summary>
    public IEnumerable<OriginalSourceCandidate> FindCandidates() => candidates
        .Where(candidate => Directory.Exists(candidate.Path))
        .OrderByDescending(candidate => candidate.Priority)
        .ThenBy(candidate => candidate.Path, StringComparer.OrdinalIgnoreCase);
}

/// <summary>Combines several locators.</summary>
/// <param name="locators">The locators to query, in order.</param>
public sealed class CompositeSourceLocator(
    IEnumerable<IOriginalSourceLocator> locators) : IOriginalSourceLocator
{
    /// <summary>
    /// Every locator's candidates, keeping the first of any that resolve to the same full path, highest
    /// priority first.
    /// </summary>
    public IEnumerable<OriginalSourceCandidate> FindCandidates() => locators
        .SelectMany(locator => locator.FindCandidates())
        .DistinctBy(candidate => Path.GetFullPath(candidate.Path), StringComparer.OrdinalIgnoreCase)
        .OrderByDescending(candidate => candidate.Priority);
}
